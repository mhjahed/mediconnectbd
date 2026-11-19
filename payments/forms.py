from django import forms
from accounts.forms import NeuFormMixin
from .models import Payment


class PaymentSubmissionForm(NeuFormMixin, forms.ModelForm):
    payment_consent = forms.BooleanField(
        required=True,
        label='I confirm this transaction reference is accurate and accept the 24-hour refund policy.',
    )

    class Meta:
        model = Payment
        fields = ['payment_method', 'sender_number', 'transaction_id', 'notes']
        widgets = {
            'sender_number': forms.TextInput(attrs={'placeholder': 'e.g. 017XXXXXXXX or A/C last 4 digits'}),
            'transaction_id': forms.TextInput(attrs={'placeholder': 'e.g. BK94J2M8Q1'}),
            'notes': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional transfer reference note'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._style_fields()

    def clean_transaction_id(self):
        trx = self.cleaned_data['transaction_id'].strip().upper()
        if len(trx) < 5:
            raise forms.ValidationError('Enter a valid transaction reference (minimum 5 characters).')
        if Payment.objects.filter(transaction_id__iexact=trx).exists():
            raise forms.ValidationError('This transaction ID has already been submitted.')
        return trx
