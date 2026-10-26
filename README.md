# CreditCart

CreditCart is a demo credit and subscription web application built with
**FastAPI**, **SQLite**, **Stripe Checkout**, and a lightweight
HTML/CSS/JavaScript frontend. Users can purchase credit packs, subscribe
to a Pro plan, use credits, view purchase history, manage billing
through Stripe Customer Portal, and request eligible refunds.

> **Project status:** This repository contains a demo-user
> implementation. Configure Stripe in test mode and verify webhook
> delivery before relying on payment or refund flows.

## Features

-   **Credit balance:** View the current credit balance.
-   **Credit packs:** Create a Stripe Checkout session for a one-time
    credit-pack purchase. The webhook grants 100 credits after a
    qualifying paid Checkout event.
-   **Pro subscription:** Create a Stripe Checkout subscription session.
    The `invoice.paid` webhook grants 1,000 credits for eligible
    subscription invoices.
-   **Credit usage:** Deduct one credit when the user uses the credit
    feature; requests fail when no credits are available.
-   **Purchase history:** Show recent credit-pack purchases.
-   **Subscription tracking:** Store Stripe subscription IDs and their
    status.
-   **Billing portal:** Open Stripe Customer Portal for the configured
    customer.
-   **Refund handling:** Check existing Stripe refunds, avoid creating
    duplicate refunds where detected, and reconcile local credits after
    a confirmed refund.
-   **Credit ledger:** Record credit additions and deductions to help
    reconcile the stored balance.
-   **Webhook safeguards:** Verify Stripe webhook signatures and record
    processed event IDs to help prevent duplicate processing.
-   **Responsive interface:** Static frontend styled for desktop and
    mobile layouts.

## Tech Stack

-   Python
-   FastAPI
-   SQLite
-   Stripe Python SDK
-   Stripe Checkout, Billing Portal, and webhooks
-   HTML, CSS, and JavaScript
-   `python-dotenv`
-   Uvicorn

## Project Structure

``` text
creditcart/
├── app.py
├── .env
├── creditcart.db          # Created/used by the application
└── static/
    ├── index.html
    ├── style.css
    └── app.js
```

Do not commit `.env`, Stripe secret keys, or other credentials to
version control.

## Requirements

-   Python 3.10+ recommended
-   A Stripe account with test-mode access
-   Stripe CLI for local webhook testing
-   A Stripe test Price ID for the credit pack
-   A Stripe test Price ID for the Pro subscription

## Setup

### 1. Open the project directory

``` bash
cd "/Users/apple/Desktop/other/NeuroTeach/creditcart"
```

Use the actual path where you saved the project if it differs.

### 2. Create and activate a virtual environment

``` bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

``` bash
pip install fastapi uvicorn stripe python-dotenv
```

If the project already has a `requirements.txt`, install from it
instead:

``` bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Create a `.env` file in the project root:

``` dotenv
STRIPE_SECRET_KEY=sk_test_REPLACE_WITH_YOUR_TEST_SECRET_KEY
STRIPE_CREDIT_PRICE_ID=price_REPLACE_WITH_CREDIT_PACK_PRICE_ID
STRIPE_PRO_PRICE_ID=price_REPLACE_WITH_PRO_SUBSCRIPTION_PRICE_ID
STRIPE_WEBHOOK_SECRET=whsec_REPLACE_WITH_WEBHOOK_SIGNING_SECRET
DATABASE_PATH=creditcart.db
```

Replace the placeholder values with your own Stripe **test-mode**
values. Never share or commit secret keys.

The code expects the credit-pack Price ID to represent the configured
credit-pack product and the Pro Price ID to represent the Pro
subscription. The refund logic currently filters for paid Pro invoices
of **\$10.00 (1,000 cents)**, so its configured price and refund
assumptions should match.

## Run the application

Start the FastAPI server:

``` bash
uvicorn app:app --reload --port 8000
```

Open the app in your browser:

``` text
http://localhost:8000
```

FastAPI's interactive API documentation is available at:

``` text
http://localhost:8000/docs
```

## Stripe Webhook Setup (Local Testing)

1.  Install and authenticate the Stripe CLI.
2.  Start the FastAPI application on port `8000`.
3.  In a separate terminal, run:

``` bash
stripe listen \
  --events checkout.session.completed,invoice.paid,customer.subscription.updated,customer.subscription.deleted,charge.refunded,refund.updated \
  --forward-to localhost:8000/api/webhook
```

4.  Copy the webhook signing secret (`whsec_...`) printed by Stripe CLI
    into `STRIPE_WEBHOOK_SECRET` in `.env`.
5.  Restart the FastAPI application after changing `.env`.
6.  Make a test payment or subscription and confirm that the CLI shows
    events and the FastAPI terminal receives `POST /api/webhook`.

The signing secret from `stripe listen` is for that CLI listener
session. If you restart the listener and receive a new secret, update
`.env` accordingly.

**Important:** Returning to the success URL does not prove that credits
were granted. The application relies on webhook events to update
payment-related records and balances.

## API Endpoints

  -------------------------------------------------------------------------
  Method                  Endpoint                  Purpose
  ----------------------- ------------------------- -----------------------
  `GET`                   `/`                       Serve the main frontend
                                                    page

  `GET`                   `/api/state`              Return the demo user's
                                                    credits, email,
                                                    purchases, and
                                                    subscriptions

  `POST`                  `/api/use-credit`         Use one available
                                                    credit

  `POST`                  `/api/checkout/credits`   Create a credit-pack
                                                    Checkout session

  `POST`                  `/api/checkout/pro`       Create a Pro
                                                    subscription Checkout
                                                    session

  `POST`                  `/api/portal`             Create a Stripe
                                                    Customer Portal session

  `POST`                  `/api/refund`             Request/check an
                                                    eligible Pro invoice
                                                    refund

  `POST`                  `/api/webhook`            Receive and process
                                                    Stripe webhook events
  -------------------------------------------------------------------------

## Database Tables

-   **`users`** --- demo user, email, Stripe customer ID, and stored
    credit balance.
-   **`processed_events`** --- Stripe event IDs already processed.
-   **`purchases`** --- credit-pack purchase records, amounts, credits,
    and refund state.
-   **`credit_ledger`** --- credit additions and deductions, including
    references used for idempotency.
-   **`subscriptions`** --- Stripe subscription IDs, user mapping, and
    status.

The app creates these tables if they do not already exist and inserts a
demo user (`demo-user`) with email `demo@example.com`.

## Credit and Payment Flow

### Credit pack

1.  The frontend requests `/api/checkout/credits`.
2.  FastAPI creates a Stripe Checkout session.
3.  The customer completes the test payment.
4.  Stripe sends `checkout.session.completed`.
5.  The webhook checks the event, saves the purchase, and grants 100
    credits.

### Pro subscription

1.  The frontend requests `/api/checkout/pro`.
2.  FastAPI creates a subscription-mode Checkout session.
3.  Stripe sends Checkout and invoice events.
4.  The application records the subscription mapping.
5.  For eligible `invoice.paid` events, the webhook grants 1,000 credits
    once per invoice.

### Credit usage and refunds

-   Using a credit reduces the available balance by one and records a
    ledger entry.
-   Refund handlers inspect Stripe refund status and reconcile
    corresponding local credit records after successful refunds.
-   Existing refund detection and unique ledger references help avoid
    duplicate refund processing. Always verify the final status in the
    Stripe Dashboard during testing.

## Troubleshooting

### Credits did not update after payment

-   Check that `stripe listen` is running and forwarding to
    `localhost:8000/api/webhook`.
-   Check that the CLI prints the relevant event after test Checkout.
-   Check that the FastAPI terminal shows a `POST /api/webhook` request.
-   Verify `STRIPE_WEBHOOK_SECRET` matches the active CLI listener.
-   Verify Stripe test Price IDs and payment status.
-   For Pro credits, check that `invoice.paid` was received and the
    subscription is mapped to `demo-user`.
-   Inspect Stripe Dashboard events and the local database before making
    another test payment.

### Stripe configuration error

-   Confirm that `STRIPE_SECRET_KEY` starts with `sk_test_`.
-   Confirm that both Price IDs start with `price_`.
-   Confirm that the webhook signing secret starts with `whsec_`.

### No credits available

The `/api/use-credit` endpoint returns an error when the demo user's
balance is zero. Confirm that a qualifying payment webhook was processed
before retrying.

## Security and Demo Limitations

-   This is a demo implementation centered on one hard-coded
    `demo-user`, not a production multi-user authentication system.
-   Keep Stripe secret keys and webhook secrets private.
-   Use Stripe test mode while developing.
-   Test webhook signature verification, repeated event delivery,
    payment failures, partial refunds, and full refunds before
    deployment.
-   Review database transaction boundaries and refund behavior before
    production use.
-   Do not treat a frontend redirect as proof of payment; verify the
    Stripe webhook and payment status.

## Future Improvements

-   Add real user authentication and per-user authorization.
-   Add automated tests for payment, subscription, and refund flows.
-   Add structured logging and clearer payment-status reporting.
-   Improve production deployment, database migrations, and webhook
    monitoring.
-   Add CI checks and a deployment guide.

## License

Add a license here if you plan to distribute this project publicly.
