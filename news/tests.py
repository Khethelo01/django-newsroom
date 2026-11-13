from unittest.mock import patch
import requests
from django.core import mail
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from .models import (
    User,
    Publisher,
    Article,
    Newsletter,
    PublicationDelivery,
    ApprovalLog,
)
from .services import deliver_article


@override_settings(
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"
)
class NewsTests(TestCase):
    def setUp(self):
        self.reader = User.objects.create_user(
            "reader", email="reader@example.com", role="reader"
        )
        self.writer = User.objects.create_user("writer", role="journalist")
        self.other = User.objects.create_user("other", role="journalist")
        self.editor = User.objects.create_user("editor", role="editor")
        self.publisher = Publisher.objects.create(name="Daily News")
        self.publisher.journalists.add(self.writer)
        self.publisher.editors.add(self.editor)
        self.article = Article.objects.create(
            title="Draft", content="Story", author=self.writer
        )
        self.published = Article.objects.create(
            title="Published",
            content="Public",
            author=self.writer,
            approved=True,
            publisher=self.publisher,
        )
        self.independent = Article.objects.create(
            title="Independent",
            content="Public",
            author=self.other,
            approved=True,
        )
        self.api = APIClient()

    def test_role_group(self):
        self.assertTrue(self.reader.groups.filter(name="Reader").exists())
        self.assertTrue(self.writer.groups.filter(name="Journalist").exists())

    def test_irrelevant_fields_none(self):
        self.assertIsNone(self.writer.reader_subscriptions)
        self.assertIsNone(self.reader.independent_articles)

    def test_non_reader_subscriptions_blocked(self):
        from django.core.exceptions import ValidationError

        with self.assertRaises(ValidationError):
            self.writer.publisher_subscriptions.add(self.publisher)

    def test_authentication_required(self):
        self.assertEqual(self.api.get("/api/articles/").status_code, 401)

    def test_all_list_only_approved(self):
        self.api.force_authenticate(self.reader)
        self.assertEqual(
            {x["id"] for x in self.api.get("/api/articles/").data},
            {self.published.pk, self.independent.pk},
        )

    def test_publisher_subscriptions(self):
        self.reader.publisher_subscriptions.add(self.publisher)
        self.api.force_authenticate(self.reader)
        self.assertEqual(
            [x["id"] for x in self.api.get("/api/articles/subscribed/").data],
            [self.published.pk],
        )

    def test_journalist_subscriptions(self):
        self.reader.journalist_subscriptions.add(self.other)
        self.api.force_authenticate(self.reader)
        self.assertEqual(
            [x["id"] for x in self.api.get("/api/articles/subscribed/").data],
            [self.independent.pk],
        )

    def test_empty_subscriptions(self):
        self.api.force_authenticate(self.reader)
        self.assertEqual(self.api.get("/api/articles/subscribed/").data, [])

    def test_journalist_create_ignores_approval_input(self):
        self.api.force_authenticate(self.writer)
        result = self.api.post(
            "/api/articles/",
            {"title": "New", "content": "Text", "approved": True},
            format="json",
        )
        self.assertEqual(result.status_code, 201)
        self.assertFalse(result.data["approved"])

    def test_reader_and_editor_cannot_create_article(self):
        for user in [self.reader, self.editor]:
            self.api.force_authenticate(user)
            self.assertEqual(
                self.api.post("/api/articles/", {}).status_code, 403
            )

    def test_foreign_publisher_rejected(self):
        self.api.force_authenticate(self.other)
        result = self.api.post(
            "/api/articles/",
            {
                "title": "Bad",
                "content": "Text",
                "publisher": self.publisher.pk,
            },
            format="json",
        )
        self.assertEqual(result.status_code, 400)

    def test_reader_cannot_read_draft(self):
        self.api.force_authenticate(self.reader)
        self.assertEqual(
            self.api.get(f"/api/articles/{self.article.pk}/").status_code, 404
        )

    def test_foreign_writer_cannot_modify(self):
        self.api.force_authenticate(self.other)
        self.assertEqual(
            self.api.put(f"/api/articles/{self.article.pk}/", {}).status_code,
            403,
        )
        self.assertEqual(
            self.api.delete(f"/api/articles/{self.article.pk}/").status_code,
            403,
        )

    def test_reader_cannot_modify(self):
        self.api.force_authenticate(self.reader)
        self.assertEqual(
            self.api.delete(f"/api/articles/{self.published.pk}/").status_code,
            403,
        )

    def test_editor_delete(self):
        self.api.force_authenticate(self.editor)
        self.assertEqual(
            self.api.delete(f"/api/articles/{self.article.pk}/").status_code,
            204,
        )

    def test_update_requires_reapproval(self):
        self.api.force_authenticate(self.writer)
        result = self.api.put(
            f"/api/articles/{self.published.pk}/",
            {
                "title": "Changed",
                "content": "Changed",
                "publisher": self.publisher.pk,
            },
            format="json",
        )
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.data["approved"])

    def test_only_editor_can_approve(self):
        for user in [self.reader, self.writer]:
            self.api.force_authenticate(user)
            self.assertEqual(
                self.api.post(
                    f"/api/articles/{self.article.pk}/approve/"
                ).status_code,
                403,
            )

    @patch("news.services.deliver_article")
    def test_editor_approves_after_commit(self, deliver):
        self.api.force_authenticate(self.editor)
        with self.captureOnCommitCallbacks(execute=True):
            result = self.api.post(f"/api/articles/{self.article.pk}/approve/")
        self.assertEqual(result.status_code, 200)
        deliver.assert_called_once_with(self.article.pk, self.editor.pk)

    @patch("news.services.requests.post")
    def test_delivery_and_idempotent_retry(self, post):
        self.reader.publisher_subscriptions.add(self.publisher)
        deliver_article(self.published.pk, self.editor.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["reader@example.com"])
        self.assertEqual(
            post.call_args.kwargs["json"], {"article": self.published.pk}
        )
        deliver_article(self.published.pk, self.editor.pk)
        self.assertEqual(post.call_count, 1)

    @patch(
        "news.services.requests.post", side_effect=requests.Timeout("offline")
    )
    def test_delivery_failure_recorded(self, post):
        deliver_article(self.published.pk, self.editor.pk)
        delivery = PublicationDelivery.objects.get(article=self.published)
        self.assertFalse(delivery.api_sent)
        self.assertIn("offline", delivery.last_error)

    def test_approval_log_protected_idempotent(self):
        self.api.force_authenticate(self.reader)
        self.assertEqual(
            self.api.post(
                "/api/approved/", {"article": self.published.pk}
            ).status_code,
            403,
        )
        self.api.force_authenticate(self.editor)
        self.assertEqual(
            self.api.post(
                "/api/approved/", {"article": self.published.pk}
            ).status_code,
            201,
        )
        self.assertEqual(
            self.api.post(
                "/api/approved/", {"article": self.published.pk}
            ).status_code,
            200,
        )
        self.assertEqual(ApprovalLog.objects.count(), 1)

    def test_newsletter_create_read_and_reader_write_denied(self):
        self.api.force_authenticate(self.writer)
        result = self.api.post(
            "/api/newsletters/",
            {
                "title": "Weekly",
                "description": "Highlights",
                "articles": [self.published.pk],
            },
            format="json",
        )
        self.assertEqual(result.status_code, 201)
        self.api.force_authenticate(self.reader)
        self.assertEqual(
            self.api.get("/api/newsletters/").data[0]["title"], "Weekly"
        )
        self.assertEqual(
            self.api.post("/api/newsletters/", {}).status_code, 403
        )

    def test_newsletter_rejects_drafts(self):
        self.api.force_authenticate(self.writer)
        result = self.api.post(
            "/api/newsletters/",
            {
                "title": "Weekly",
                "description": "Text",
                "articles": [self.article.pk],
            },
            format="json",
        )
        self.assertEqual(result.status_code, 400)

    def test_newsletter_owner_checks(self):
        obj = Newsletter.objects.create(
            title="Weekly", description="Text", author=self.writer
        )
        self.api.force_authenticate(self.other)
        self.assertEqual(
            self.api.delete(f"/api/newsletters/{obj.pk}/").status_code, 403
        )
        self.api.force_authenticate(self.editor)
        self.assertEqual(
            self.api.delete(f"/api/newsletters/{obj.pk}/").status_code, 204
        )

    def test_token_authentication(self):
        self.reader.set_password("Password!234")
        self.reader.save()
        result = self.api.post(
            "/api/token/", {"username": "reader", "password": "Password!234"}
        )
        self.assertEqual(result.status_code, 200)
        self.api.credentials(
            HTTP_AUTHORIZATION="Token " + result.data["token"]
        )
        self.assertEqual(self.api.get("/api/articles/").status_code, 200)
