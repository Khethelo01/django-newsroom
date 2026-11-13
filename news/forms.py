from django import forms
from django.contrib.auth.forms import UserCreationForm
from .models import User, Article, Newsletter, Publisher


class RegisterForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["username", "email", "password1", "password2"]


class ArticleForm(forms.ModelForm):
    class Meta:
        model = Article
        fields = ["title", "content", "publisher"]

    def __init__(self, *args, author, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.author = author
        self.fields["publisher"].queryset = Publisher.objects.filter(
            journalists=author
        )


class NewsletterForm(forms.ModelForm):
    class Meta:
        model = Newsletter
        fields = ["title", "description", "publisher", "articles"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["articles"].queryset = Article.objects.filter(
            approved=True
        )


class SubscriptionForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["publisher_subscriptions", "journalist_subscriptions"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["journalist_subscriptions"].queryset = User.objects.filter(
            role="journalist"
        )
