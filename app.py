
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import stripe

BASE = Path(__file__).parent
load_dotenv(BASE / ".env")

DB_PATH = os.getenv("DATABASE_PATH", str(BASE / "creditcart.db"))
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "")

app = FastAPI(title="CreditCart", version="1.1.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            email TEXT,
            stripe_customer_id TEXT UNIQUE,
            credits INTEGER NOT NULL DEFAULT 0 CHECK(credits >= 0)
        );

        CREATE TABLE IF NOT EXISTS processed_events (
            event_id TEXT PRIMARY KEY,
            processed_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            checkout_session_id TEXT UNIQUE,
            payment_intent_id TEXT UNIQUE,
            amount INTEGER NOT NULL,
            credits INTEGER NOT NULL,
            refunded INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS credit_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            delta INTEGER NOT NULL,
            reason TEXT NOT NULL,
            reference_id TEXT UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS subscriptions (
            stripe_subscription_id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """)

        c.execute("""
            INSERT OR IGNORE INTO users(id,email)
            VALUES('demo-user','demo@example.com')
        """)


init_db()


def require_stripe():
    if not stripe.api_key or not stripe.api_key.startswith("sk_test_"):
        raise HTTPException(
            503,
            "Configure a Stripe sandbox secret key (sk_test_...) in .env"
        )


def demo_user():
    with db() as c:
        row = c.execute(
            "SELECT * FROM users WHERE id='demo-user'"
        ).fetchone()
        return dict(row)


def sync_user_balance(c, user_id):
    """
    Reconcile the displayed/stored balance with the credit ledger.
    Negative balances are not permitted.
    """
    row = c.execute("""
        SELECT COALESCE(SUM(delta), 0) AS balance
        FROM credit_ledger
        WHERE user_id=?
    """, (user_id,)).fetchone()

    balance = max(0, int(row["balance"] or 0))

    c.execute("""
        UPDATE users SET credits=?
        WHERE id=?
    """, (balance, user_id))

    return balance


def apply_pro_refund_reversal(c, invoice_id):
    """
    Record a Pro credit reversal once, after Stripe confirms success.
    Never create a negative available-credit balance.
    """
    invoice_ref = f"invoice-{invoice_id}"
    refund_ref = f"pro-refund-invoice-{invoice_id}"

    grant = c.execute("""
        SELECT user_id, delta
        FROM credit_ledger
        WHERE reference_id=?
          AND reason='monthly_pro_credits'
    """, (invoice_ref,)).fetchone()

    if not grant:
        return {
            "applied": False,
            "message": "No matching Pro credit grant was found."
        }

    existing = c.execute("""
        SELECT id
        FROM credit_ledger
        WHERE reference_id=?
    """, (refund_ref,)).fetchone()

    if existing:
        balance = sync_user_balance(c, grant["user_id"])
        return {
            "applied": False,
            "already_recorded": True,
            "credits": balance,
            "message": "This invoice's credit reversal was already recorded."
        }

    current_balance = sync_user_balance(c, grant["user_id"])

    # Reverse available credits only. Never allow a negative balance.
    reversal = min(current_balance, max(0, int(grant["delta"])))

    if reversal > 0:
        c.execute("""
            INSERT INTO credit_ledger(
                user_id, delta, reason, reference_id
            ) VALUES(?, ?, 'pro_refund', ?)
        """, (
            grant["user_id"],
            -reversal,
            refund_ref,
        ))

    balance = sync_user_balance(c, grant["user_id"])

    return {
        "applied": True,
        "credits_reversed": reversal,
        "credits": balance,
        "message": (
            "Stripe confirmed the refund. Local credits were reconciled."
        )
    }


def invoice_payment_intent(invoice_id):
    """Resolve the PaymentIntent linked to a Stripe invoice."""
    payments = stripe.InvoicePayment.list(
        invoice=invoice_id,
        limit=100,
    )

    for invoice_payment in payments.data:
        payment = invoice_payment.get("payment") or {}

        if payment.get("type") != "payment_intent":
            continue

        payment_intent = payment.get("payment_intent")

        if isinstance(payment_intent, dict):
            payment_intent = payment_intent.get("id")

        if payment_intent:
            return payment_intent

    return None


def list_payment_refunds(payment_intent):
    """Return Stripe refunds already associated with a PaymentIntent."""
    refunds = stripe.Refund.list(
        payment_intent=payment_intent,
        limit=100,
    )
    return refunds.data


def find_confirmed_refund(payment_intent, amount):
    """
    Find a successful full refund, or a refund still being processed.
    Partial refunds are not treated as full refunds.
    """
    refunds = list_payment_refunds(payment_intent)

    successful_full = None
    pending_refund = None

    for item in refunds:
        status = item.get("status")
        refunded_amount = int(item.get("amount") or 0)

        if status == "succeeded" and refunded_amount >= amount:
            successful_full = item
            break

        if status in ("pending", "requires_action"):
            pending_refund = item

    return successful_full, pending_refund


@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")


@app.get("/api/state")
def state():
    user = demo_user()

    with db() as c:
        # Fix stale stored balances left by previous manual ledger changes.
        balance = sync_user_balance(c, user["id"])

        purchases = [
            dict(r) for r in c.execute("""
                SELECT id, amount, credits, refunded, created_at
                FROM purchases
                WHERE user_id=?
                ORDER BY id DESC
                LIMIT 10
            """, (user["id"],))
        ]

        subscriptions = [
            dict(r) for r in c.execute("""
                SELECT stripe_subscription_id, status
                FROM subscriptions
                WHERE user_id=?
                ORDER BY rowid DESC
            """, (user["id"],))
        ]

    return {
        "credits": balance,
        "email": user["email"],
        "purchases": purchases,
        "subscriptions": subscriptions,
    }


@app.post("/api/use-credit")
def use_credit():
    with db() as c:
        sync_user_balance(c, "demo-user")

        c.execute("""
            UPDATE users
            SET credits=credits-1
            WHERE id='demo-user' AND credits>0
        """)

        if c.execute("SELECT changes()").fetchone()[0] == 0:
            raise HTTPException(400, "Not enough credits")

        c.execute("""
            INSERT INTO credit_ledger(
                user_id, delta, reason, reference_id
            )
            VALUES(
                'demo-user', -1, 'used', lower(hex(randomblob(16)))
            )
        """)

        balance = c.execute("""
            SELECT credits FROM users WHERE id='demo-user'
        """).fetchone()["credits"]

    return {"credits": balance}


@app.post("/api/checkout/{kind}")
def checkout(kind: str, request: Request):
    require_stripe()

    if kind not in ("credits", "pro"):
        raise HTTPException(404, "Unknown product")

    price = os.getenv(
        "STRIPE_CREDIT_PRICE_ID" if kind == "credits"
        else "STRIPE_PRO_PRICE_ID",
        "",
    )

    if not price.startswith("price_"):
        raise HTTPException(
            503,
            "Set the corresponding Stripe test Price ID in .env",
        )

    user = demo_user()
    customer_id = user["stripe_customer_id"]

    if not customer_id:
        customer = stripe.Customer.create(
            email=user["email"],
            metadata={"creditcart_user_id": user["id"]},
            idempotency_key="creditcart-customer-demo-user",
        )
        customer_id = customer.id

        with db() as c:
            c.execute("""
                UPDATE users SET stripe_customer_id=?
                WHERE id='demo-user'
                  AND stripe_customer_id IS NULL
            """, (customer_id,))

        customer_id = demo_user()["stripe_customer_id"]

    origin = str(request.base_url).rstrip("/")
    mode = "payment" if kind == "credits" else "subscription"

    session = stripe.checkout.Session.create(
        mode=mode,
        customer=customer_id,
        line_items=[{"price": price, "quantity": 1}],
        success_url=origin + "/?checkout=return",
        cancel_url=origin + "/?checkout=cancelled",
        client_reference_id="demo-user",
        metadata={
            "creditcart_user_id": "demo-user",
            "purchase_kind": kind,
        },
        subscription_data={
            "metadata": {"creditcart_user_id": "demo-user"}
        } if kind == "pro" else {},
        idempotency_key=f"checkout-{kind}-{os.urandom(16).hex()}",
    )

    return {"url": session.url}


@app.post("/api/portal")
def portal(request: Request):
    require_stripe()
    user = demo_user()

    if not user["stripe_customer_id"]:
        raise HTTPException(
            400,
            "Subscribe first to open the Customer Portal",
        )

    session = stripe.billing_portal.Session.create(
        customer=user["stripe_customer_id"],
        return_url=str(request.base_url),
    )

    return {"url": session.url}


@app.post("/api/refund")
def refund():
    """
    Refund the latest eligible paid $10 Pro invoice.

    Existing Stripe refunds are detected before a new refund is created.
    Local credit reversals are applied only after Stripe confirms success.
    """
    require_stripe()

    with db() as c:
        subscriptions = c.execute("""
            SELECT stripe_subscription_id
            FROM subscriptions
            WHERE user_id='demo-user'
            ORDER BY rowid DESC
        """).fetchall()

    if not subscriptions:
        raise HTTPException(400, "No Pro subscription found")

    try:
        candidates = []

        for sub_row in subscriptions:
            sub_id = sub_row["stripe_subscription_id"]

            invoices = stripe.Invoice.list(
                subscription=sub_id,
                limit=100,
            )

            for invoice in invoices.data:
                if invoice.get("status") != "paid":
                    continue

                amount_paid = int(invoice.get("amount_paid") or 0)

                # Pro monthly price is $10.00 (1000 cents).
                if amount_paid != 1000:
                    continue

                invoice_id = invoice["id"]

                with db() as c:
                    grant = c.execute("""
                        SELECT user_id, delta
                        FROM credit_ledger
                        WHERE reference_id=?
                          AND reason='monthly_pro_credits'
                    """, (f"invoice-{invoice_id}",)).fetchone()

                    local_reversal = c.execute("""
                        SELECT id
                        FROM credit_ledger
                        WHERE reference_id=?
                    """, (
                        f"pro-refund-invoice-{invoice_id}",
                    )).fetchone()

                if grant:
                    candidates.append({
                        "invoice": invoice,
                        "amount": amount_paid,
                        "local_reversal": bool(local_reversal),
                    })

        if not candidates:
            raise HTTPException(
                400,
                "No paid $10 Pro invoice with a matching credit grant "
                "was found. Check Stripe Dashboard and your subscriptions.",
            )

        # Inspect newest invoices first across all stored subscriptions.
        candidates.sort(
            key=lambda item: int(item["invoice"].get("created") or 0),
            reverse=True,
        )

        selected = None
        pending_invoice_id = None

        for candidate in candidates:
            invoice = candidate["invoice"]
            invoice_id = invoice["id"]

            payment_intent = invoice_payment_intent(invoice_id)

            if not payment_intent:
                continue

            succeeded, pending = find_confirmed_refund(
                payment_intent,
                candidate["amount"],
            )

            if succeeded:
                with db() as c:
                    result = apply_pro_refund_reversal(c, invoice_id)

                return {
                    "status": "already_refunded",
                    "invoice_id": invoice_id,
                    "credits": result.get("credits"),
                    "message": (
                        "Stripe confirms this invoice was already fully "
                        "refunded. No duplicate refund was created. "
                        "Local credits have been reconciled."
                    ),
                }

            if pending:
                pending_invoice_id = invoice_id
                continue

            # A local reversal alone is not proof of a Stripe refund.
            # Never issue another refund automatically for that invoice.
            if candidate["local_reversal"]:
                continue

            selected = {
                "invoice_id": invoice_id,
                "payment_intent": payment_intent,
                "amount": candidate["amount"],
            }
            break

        if pending_invoice_id:
            raise HTTPException(
                409,
                "A refund for a Pro invoice is still processing in Stripe. "
                "Wait for its final status before trying again.",
            )

        if not selected:
            raise HTTPException(
                409,
                "No safe invoice was found for a new refund. A refund may "
                "already exist or local credits may already be reversed. "
                "Check Stripe Dashboard before taking further action.",
            )

        invoice_id = selected["invoice_id"]
        payment_intent = selected["payment_intent"]

        # Check once more immediately before creating a refund.
        succeeded, pending = find_confirmed_refund(
            payment_intent,
            selected["amount"],
        )

        if succeeded:
            with db() as c:
                result = apply_pro_refund_reversal(c, invoice_id)

            return {
                "status": "already_refunded",
                "invoice_id": invoice_id,
                "credits": result.get("credits"),
                "message": (
                    "This invoice was already refunded. "
                    "No duplicate refund was created."
                ),
            }

        if pending:
            raise HTTPException(
                409,
                "A refund for this invoice is already processing.",
            )

        refund_obj = stripe.Refund.create(
            payment_intent=payment_intent,
            amount=selected["amount"],
            metadata={
                "creditcart_refund_kind": "pro",
                "creditcart_invoice_id": invoice_id,
                "creditcart_user_id": "demo-user",
            },
            idempotency_key=f"creditcart-pro-refund-{invoice_id}",
        )

        # If Stripe confirms success immediately, reconcile now.
        # The webhook can safely run later because the operation is idempotent.
        if refund_obj.get("status") == "succeeded":
            with db() as c:
                result = apply_pro_refund_reversal(c, invoice_id)

            return {
                "status": "succeeded",
                "invoice_id": invoice_id,
                "credits": result.get("credits"),
                "message": (
                    "Stripe confirmed the refund and local credits "
                    "were reconciled."
                ),
            }

        return {
            "status": refund_obj.get("status"),
            "invoice_id": invoice_id,
            "message": (
                "Stripe accepted the refund request. Local credits will "
                "be reconciled when Stripe confirms the refund succeeded."
            ),
        }

    except HTTPException:
        raise
    except stripe.error.StripeError as exc:
        # Stripe may report an already-refunded charge. Check the payment
        # records again rather than blindly retrying the refund.
        try:
            for sub_row in subscriptions:
                invoices = stripe.Invoice.list(
                    subscription=sub_row["stripe_subscription_id"],
                    limit=100,
                )

                for invoice in invoices.data:
                    if (
                        invoice.get("status") != "paid"
                        or int(invoice.get("amount_paid") or 0) != 1000
                    ):
                        continue

                    invoice_id = invoice["id"]

                    with db() as c:
                        grant = c.execute("""
                            SELECT user_id
                            FROM credit_ledger
                            WHERE reference_id=?
                              AND reason='monthly_pro_credits'
                        """, (f"invoice-{invoice_id}",)).fetchone()

                    if not grant:
                        continue

                    pi = invoice_payment_intent(invoice_id)
                    if not pi:
                        continue

                    succeeded, _ = find_confirmed_refund(pi, 1000)

                    if succeeded:
                        with db() as c:
                            result = apply_pro_refund_reversal(
                                c, invoice_id
                            )

                        return {
                            "status": "already_refunded",
                            "invoice_id": invoice_id,
                            "credits": result.get("credits"),
                            "message": (
                                "Stripe confirms this invoice was already "
                                "refunded. Local credits were reconciled; "
                                "no duplicate refund was created."
                            ),
                        }
        except stripe.error.StripeError:
            pass

        raise HTTPException(502, f"Stripe error: {str(exc)}")


@app.post("/api/webhook")
async def webhook(request: Request):
    require_stripe()

    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")

    if not secret:
        raise HTTPException(
            503,
            "Set STRIPE_WEBHOOK_SECRET from Stripe CLI/webhook endpoint",
        )

    try:
        event = stripe.Webhook.construct_event(payload, sig, secret)
    except (ValueError, stripe.error.SignatureVerificationError):
        raise HTTPException(400, "Invalid webhook signature or payload")

    event_id = event["id"]
    typ = event["type"]
    obj = event["data"]["object"]

    with db() as c:
        try:
            c.execute(
                "INSERT INTO processed_events(event_id) VALUES(?)",
                (event_id,),
            )
        except sqlite3.IntegrityError:
            return {"received": True, "duplicate": True}

        if typ == "checkout.session.completed":
            session = obj

            if (
                session.get("payment_status") == "paid"
                and session.get("mode") == "payment"
                and (session.get("metadata") or {}).get("purchase_kind")
                == "credits"
            ):
                metadata = session.get("metadata") or {}
                uid = metadata.get("creditcart_user_id")
                pi = session.get("payment_intent")
                sid = session.get("id")

                if uid == "demo-user" and pi and sid:
                    exists = c.execute("""
                        SELECT id FROM purchases
                        WHERE checkout_session_id=?
                    """, (sid,)).fetchone()

                    if not exists:
                        c.execute("""
                            INSERT INTO purchases(
                                user_id, checkout_session_id,
                                payment_intent_id, amount, credits
                            ) VALUES(?,?,?,?,100)
                        """, (
                            uid,
                            sid,
                            pi,
                            int(session.get("amount_total") or 500),
                        ))

                        c.execute("""
                            UPDATE users SET credits=credits+100
                            WHERE id=?
                        """, (uid,))

                        c.execute("""
                            INSERT OR IGNORE INTO credit_ledger(
                                user_id, delta, reason, reference_id
                            ) VALUES(?, 100, ?, ?)
                        """, (uid, "credit_pack", sid))

            if session.get("mode") == "subscription":
                metadata = session.get("metadata") or {}
                uid = (
                    metadata.get("creditcart_user_id")
                    or session.get("client_reference_id")
                )
                sub_id = session.get("subscription")

                if uid == "demo-user" and sub_id:
                    c.execute("""
                        INSERT OR IGNORE INTO subscriptions(
                            stripe_subscription_id, user_id, status
                        ) VALUES(?,?,?)
                    """, (sub_id, uid, "active"))

        elif typ == "invoice.paid":
            invoice = obj
            parent = invoice.get("parent") or {}
            subscription_details = (
                parent.get("subscription_details") or {}
            )
            sub_id = (
                invoice.get("subscription")
                or subscription_details.get("subscription")
            )

            if (
                sub_id
                and invoice.get("billing_reason")
                in ("subscription_cycle", "subscription_create")
            ):
                sub_row = c.execute("""
                    SELECT user_id FROM subscriptions
                    WHERE stripe_subscription_id=?
                """, (sub_id,)).fetchone()

                if not sub_row:
                    try:
                        stripe_sub = stripe.Subscription.retrieve(sub_id)
                        uid = (
                            stripe_sub.get("metadata") or {}
                        ).get("creditcart_user_id")

                        if uid == "demo-user":
                            c.execute("""
                                INSERT OR IGNORE INTO subscriptions(
                                    stripe_subscription_id, user_id, status
                                ) VALUES(?,?,?)
                            """, (
                                sub_id,
                                uid,
                                stripe_sub.get("status", "active"),
                            ))
                            sub_row = {"user_id": uid}
                    except stripe.error.StripeError:
                        sub_row = None

                if sub_row:
                    invoice_ref = f"invoice-{invoice['id']}"

                    existing_grant = c.execute("""
                        SELECT id FROM credit_ledger
                        WHERE reference_id=?
                    """, (invoice_ref,)).fetchone()

                    if not existing_grant:
                        c.execute("""
                            INSERT INTO credit_ledger(
                                user_id, delta, reason, reference_id
                            ) VALUES(?, 1000, ?, ?)
                        """, (
                            sub_row["user_id"],
                            "monthly_pro_credits",
                            invoice_ref,
                        ))

                        c.execute("""
                            UPDATE users SET credits=credits+1000
                            WHERE id=?
                        """, (sub_row["user_id"],))

        elif typ in (
            "customer.subscription.updated",
            "customer.subscription.deleted",
        ):
            sub = obj
            c.execute("""
                UPDATE subscriptions SET status=?
                WHERE stripe_subscription_id=?
            """, (
                sub.get("status", "canceled"),
                sub["id"],
            ))

        elif typ == "refund.updated":
            refund_obj = obj
            metadata = refund_obj.get("metadata") or {}

            if (
                refund_obj.get("status") == "succeeded"
                and metadata.get("creditcart_refund_kind") == "pro"
            ):
                invoice_id = metadata.get("creditcart_invoice_id")

                if invoice_id:
                    apply_pro_refund_reversal(c, invoice_id)

        elif typ == "charge.refunded":
            charge = obj

            full_refund = (
                int(charge.get("amount_refunded") or 0)
                >= int(charge.get("amount") or 0)
            )

            # Preserve one-time credit-pack refund handling.
            if full_refund:
                payment_intent = charge.get("payment_intent")

                purchase = c.execute("""
                    SELECT * FROM purchases
                    WHERE payment_intent_id=? AND refunded=0
                """, (payment_intent,)).fetchone()

                if purchase:
                    current = sync_user_balance(c, purchase["user_id"])
                    reversal = min(current, int(purchase["credits"]))

                    if reversal > 0:
                        c.execute("""
                            INSERT OR IGNORE INTO credit_ledger(
                                user_id, delta, reason, reference_id
                            ) VALUES(?, ?, ?, ?)
                        """, (
                            purchase["user_id"],
                            -reversal,
                            "refund",
                            f"refund-purchase-{purchase['id']}",
                        ))

                    c.execute("""
                        UPDATE purchases SET refunded=1 WHERE id=?
                    """, (purchase["id"],))

                    sync_user_balance(c, purchase["user_id"])

            # Also recognize Pro refunds surfaced in the charge event.
            refunds = (charge.get("refunds") or {}).get("data") or []

            for refund_item in refunds:
                metadata = refund_item.get("metadata") or {}

                if (
                    refund_item.get("status") == "succeeded"
                    and metadata.get("creditcart_refund_kind") == "pro"
                ):
                    invoice_id = metadata.get("creditcart_invoice_id")

                    if invoice_id:
                        apply_pro_refund_reversal(c, invoice_id)

    return {"received": True}
