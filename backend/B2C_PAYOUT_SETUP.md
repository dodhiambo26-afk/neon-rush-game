# Live M-Pesa B2C prize payout setup

This feature is intentionally **admin-triggered per prize award**. It does not automatically pay on tournament expiry. An administrator must check the official results, create an award, and explicitly confirm the cash transfer.

## Player payout number

A player's payout number is populated only after a successful, validated NEON RUSH STK Push callback. The callback checks the paid amount, receipt and phone number against the pending request, then stores that phone as verified on the player's profile. A later successful STK payment made using a different phone updates the verified number.

Players without a successful M-Pesa payment on their account cannot receive a B2C prize through this feature.

## Render environment variables

Add these directly in the production backend service's Environment settings. Never put secrets in GitHub, browser code, chat, or this document.

- `MPESA_ENVIRONMENT=production`
- `MPESA_B2C_INITIATOR_NAME`: Safaricom-approved B2C initiator username.
- `MPESA_B2C_SECURITY_CREDENTIAL`: encrypted initiator password created with Safaricom's production certificate. This is not the STK passkey.
- `MPESA_B2C_SHORTCODE`: organization shortcode configured for B2C.
- `MPESA_B2C_CALLBACK_SECRET`: long random secret used in the result and timeout callback URLs.

The existing `MPESA_CONSUMER_KEY` and `MPESA_CONSUMER_SECRET` must be the live Daraja app credentials approved for B2C. Keep the STK variables (`MPESA_PASSKEY`, `MPESA_SHORTCODE`, `MPESA_TILL_NUMBER`) configured for collections; B2C does not use the STK passkey.

Safaricom must have B2C enabled for the shortcode and initiator, and the callback URLs must be reachable over HTTPS. Confirm the correct production initiator, security credential, shortcode, transaction limits and callback configuration with Safaricom before enabling live transfers.

## Admin workflow

1. Wait until the tournament period has closed.
2. Verify the official leaderboard and select the winner's Telegram ID.
3. Create a prize award in Admin Dashboard → Prizes & payouts, entering the exact period key and KES amount.
4. Review the saved award and the player's verified payout number.
5. Explicitly confirm **Send M-Pesa payout**.
6. The award moves to `processing` when the request is reserved, then to `paid` or `failed` only after Safaricom's result callback. A timeout or uncertain network result becomes `review_required`; do not retry until reconciled with Daraja.

The Firestore reservation prevents a second admin click from submitting the same award again. Do not manually reset a `processing` or `review_required` award to `pending`.

## Important

- Test with Safaricom sandbox first, then a controlled low-value live payout.
- A Daraja acceptance response means the request was received, not that the recipient was paid.
- This implementation does not calculate the top-five prize pool or automatically generate five awards. An admin creates each approved award after checking results and the amount.
- Review applicable Kenyan competition, consumer-protection, tax and payment-service requirements before running paid-entry cash-prize competitions.
