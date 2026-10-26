# CreditCart demo checklist

- [ ] Test-mode Price IDs configured ($5 one-time / $10 monthly)
- [ ] Stripe CLI webhook forwarding active and signing secret configured
- [ ] Buy 100 credits: complete test checkout; verify exactly +100 after webhook
- [ ] Replay a webhook event: verify no duplicate credit grant
- [ ] Use 1 credit: verify balance -1
- [ ] Attempt use at zero: verify rejection
- [ ] Subscribe to Pro: verify +1,000 after paid invoice webhook
- [ ] Advance Stripe test clock through a renewal: verify another +1,000
- [ ] Cancel subscription in Customer Portal
- [ ] Refund a credit pack while enough credits remain: verify Stripe refund and credit reversal
- [ ] Confirm success page alone never changes balance
