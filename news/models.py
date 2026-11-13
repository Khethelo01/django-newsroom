from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models


class User(AbstractUser):
    role = models.CharField(
        max_length=12,
        choices=[
            ("reader", "Reader"),
            ("editor", "Editor"),
            ("journalist", "Journalist"),
        ],
        default="reader",
    )
    publisher_subscriptions = models.ManyToManyField(
        "Publisher", blank=True, related_name="subscribers"
    )
    journalist_subscriptions = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="subscribers",
        limit_choices_to={"role": "journalist"},
    )

    @property
    def reader_subscriptions(self):
        if self.role != "reader":
            return None
        return (
            self.publisher_subscriptions.all(),
            self.journalist_subscriptions.all(),
        )

    @property
    def independent_articles(self):
        return (
            self.articles.filter(publisher__isnull=True)
            if self.role == "journalist"
            else None
        )

    @property
    def independent_newsletters(self):
        return (
            self.newsletters.filter(publisher__isnull=True)
            if self.role == "journalist"
            else None
        )


class Publisher(models.Model):
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    editors = models.ManyToManyField(
        User,
        blank=True,
        related_name="edited_publishers",
        limit_choices_to={"role": "editor"},
    )
    journalists = models.ManyToManyField(
        User,
        blank=True,
        related_name="publishers",
        limit_choices_to={"role": "journalist"},
    )

    def __str__(self):
        return self.name


class Article(models.Model):
    title = models.CharField(max_length=200)
    content = models.TextField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="articles",
        limit_choices_to={"role": "journalist"},
    )
    publisher = models.ForeignKey(
        Publisher,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="articles",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    approved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def clean(self):
        if self.author_id and self.author.role != "journalist":
            raise ValidationError("Articles must have a journalist author.")
        if (
            self.publisher_id
            and not self.publisher.journalists.filter(
                pk=self.author_id
            ).exists()
        ):
            raise ValidationError("The author must belong to this publisher.")

    def __str__(self):
        return self.title


class Newsletter(models.Model):
    title = models.CharField(max_length=200)
    description = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    author = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name="newsletters"
    )
    publisher = models.ForeignKey(
        Publisher, on_delete=models.PROTECT, null=True, blank=True
    )
    articles = models.ManyToManyField(
        Article, blank=True, related_name="newsletters"
    )

    def __str__(self):
        return self.title


class PublicationDelivery(models.Model):
    article = models.OneToOneField(
        Article, on_delete=models.CASCADE, related_name="delivery"
    )
    email_sent = models.BooleanField(default=False)
    api_sent = models.BooleanField(default=False)
    last_error = models.TextField(blank=True)


class ApprovalLog(models.Model):
    article = models.OneToOneField(Article, on_delete=models.CASCADE)
    logged_at = models.DateTimeField(auto_now_add=True)
