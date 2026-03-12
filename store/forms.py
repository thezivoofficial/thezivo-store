from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from unfold.widgets import (
    UnfoldAdminTextInputWidget,
    UnfoldAdminEmailInputWidget,
    UnfoldAdminPasswordToggleWidget,
)


class StaffUserCreationForm(UserCreationForm):
    """User creation form that uses Unfold's styled widgets."""

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].widget = UnfoldAdminTextInputWidget()
        self.fields["password1"].widget = UnfoldAdminPasswordToggleWidget()
        self.fields["password2"].widget = UnfoldAdminPasswordToggleWidget()
        self.fields["password1"].help_text = (
            "Min 8 characters. Not too common. Not entirely numeric."
        )
        self.fields["password2"].help_text = "Enter the same password again to confirm."
