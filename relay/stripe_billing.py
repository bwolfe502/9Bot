"""
Stripe Billing Integration

Handles Stripe Checkout, Customer Portal, and webhook processing.
All stripe.* calls are synchronous — call from ``asyncio.to_thread()``
in async handlers.

Environment variables:
    STRIPE_API_KEY             — Stripe secret key (sk_test_... or sk_live_...)
    STRIPE_WEBHOOK_SECRET      — Webhook signing secret (whsec_...)
    STRIPE_PRICE_STANDARD      — Price ID for Standard plan ($20/mo)
"""

import logging
import os
from datetime import datetime, timezone

import stripe

import portal_db as db

log = logging.getLogger("stripe_billing")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

stripe.api_key = os.environ.get("STRIPE_API_KEY", "")
WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")

PLANS = {
    "standard": {
        "price_id": os.environ.get("STRIPE_PRICE_STANDARD", ""),
        "device_limit": 1,
        "label": "Standard",
        "price": 20,
    },
}

# Reverse lookup: price_id → plan name
_PRICE_TO_PLAN = {v["price_id"]: k for k, v in PLANS.items() if v["price_id"]}


def is_configured() -> bool:
    """Return True if Stripe is properly configured."""
    return bool(stripe.api_key and WEBHOOK_SECRET)


# ---------------------------------------------------------------------------
# Checkout + Portal
# ---------------------------------------------------------------------------

def create_checkout_session(user_id: int, plan: str, success_url: str, cancel_url: str) -> str:
    """Create a Stripe Checkout Session.  Returns the checkout URL."""
    if plan not in PLANS:
        raise ValueError(f"Unknown plan: {plan}")

    plan_info = PLANS[plan]
    if not plan_info["price_id"]:
        raise ValueError(f"Price ID not configured for plan: {plan}")

    user = db.get_user_by_id(user_id)
    if not user:
        raise ValueError("User not found")

    # Reuse existing Stripe customer if we have one
    customer_id = user.get("stripe_customer_id")
    customer_kwargs = {}
    if customer_id:
        customer_kwargs["customer"] = customer_id

    session = stripe.checkout.Session.create(
        mode="subscription",
        line_items=[{"price": plan_info["price_id"], "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": str(user_id), "plan": plan},
        **customer_kwargs,
    )
    return session.url


def create_portal_session(user_id: int, return_url: str) -> str:
    """Create a Stripe Customer Portal session.  Returns the portal URL."""
    sub = db.get_subscription(user_id)
    if not sub or not sub.get("stripe_customer_id"):
        raise ValueError("No subscription found for user")

    session = stripe.billing_portal.Session.create(
        customer=sub["stripe_customer_id"],
        return_url=return_url,
    )
    return session.url


# ---------------------------------------------------------------------------
# Subscription queries
# ---------------------------------------------------------------------------

def get_subscription(user_id: int) -> dict | None:
    """Get subscription info for a user.  Returns dict or None."""
    return db.get_subscription(user_id)


def check_device_limit(user_id: int) -> tuple[int, int]:
    """Return (current_device_count, device_limit) for a user."""
    sub = db.get_subscription(user_id)
    if not sub:
        return 0, 0
    current = db.count_user_device_grants(user_id)
    return current, sub["device_limit"]


def has_active_subscription(user_id: int) -> bool:
    """Check if user has an active (or past_due grace) subscription."""
    sub = db.get_subscription(user_id)
    if not sub:
        return False
    return sub["status"] in ("active", "past_due")


# ---------------------------------------------------------------------------
# Webhook handling
# ---------------------------------------------------------------------------

def handle_webhook(payload: bytes, sig_header: str) -> dict:
    """Verify and process a Stripe webhook event.

    Returns {"status": "ok", "type": event_type} on success.
    Raises ValueError on signature verification failure.
    """
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, WEBHOOK_SECRET)
    except stripe.SignatureVerificationError:
        raise ValueError("Invalid webhook signature")

    event_type = event["type"]
    log.info("Stripe webhook: %s", event_type)

    if event_type == "checkout.session.completed":
        _on_checkout_completed(event["data"]["object"])
    elif event_type == "invoice.paid":
        _on_invoice_paid(event["data"]["object"])
    elif event_type == "invoice.payment_failed":
        _on_invoice_payment_failed(event["data"]["object"])
    elif event_type in (
        "customer.subscription.updated",
        "customer.subscription.deleted",
    ):
        _on_subscription_changed(event["data"]["object"])
    else:
        log.debug("Unhandled webhook event type: %s", event_type)

    return {"status": "ok", "type": event_type}


def _on_checkout_completed(session: dict) -> None:
    """Handle successful checkout — create/update subscription record."""
    user_id = session.get("metadata", {}).get("user_id")
    plan = session.get("metadata", {}).get("plan")
    customer_id = session.get("customer")
    subscription_id = session.get("subscription")

    if not user_id or not plan or not customer_id:
        log.warning("Checkout completed but missing metadata: %s", session.get("id"))
        return

    user_id = int(user_id)
    plan_info = PLANS.get(plan, {})
    device_limit = plan_info.get("device_limit", 0)

    # Fetch subscription details from Stripe for period end
    period_end = None
    if subscription_id:
        try:
            stripe_sub = stripe.Subscription.retrieve(subscription_id)
            period_end = datetime.fromtimestamp(
                stripe_sub["current_period_end"], tz=timezone.utc
            ).isoformat()
        except Exception as e:
            log.warning("Failed to fetch subscription details: %s", e)

    db.upsert_subscription(
        user_id=user_id,
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription_id,
        plan=plan,
        status="active",
        device_limit=device_limit,
        current_period_end=period_end,
    )
    log.info("Subscription created: user=%d plan=%s", user_id, plan)


def _on_invoice_paid(invoice: dict) -> None:
    """Handle successful payment — ensure subscription is active."""
    customer_id = invoice.get("customer")
    subscription_id = invoice.get("subscription")
    if not customer_id or not subscription_id:
        return

    user = db.get_user_by_stripe_customer(customer_id)
    if not user:
        log.warning("Invoice paid for unknown customer: %s", customer_id)
        return

    sub = db.get_subscription(user["id"])
    if sub and sub.get("stripe_subscription_id") == subscription_id:
        # Fetch updated period end
        try:
            stripe_sub = stripe.Subscription.retrieve(subscription_id)
            period_end = datetime.fromtimestamp(
                stripe_sub["current_period_end"], tz=timezone.utc
            ).isoformat()
            plan_name = _PRICE_TO_PLAN.get(
                stripe_sub["items"]["data"][0]["price"]["id"], sub["plan"]
            )
            plan_info = PLANS.get(plan_name, {})
            db.upsert_subscription(
                user_id=user["id"],
                stripe_customer_id=customer_id,
                stripe_subscription_id=subscription_id,
                plan=plan_name,
                status="active",
                device_limit=plan_info.get("device_limit", sub["device_limit"]),
                current_period_end=period_end,
            )
        except Exception as e:
            log.warning("Failed to update subscription on invoice.paid: %s", e)


def _on_invoice_payment_failed(invoice: dict) -> None:
    """Handle failed payment — mark as past_due."""
    customer_id = invoice.get("customer")
    if not customer_id:
        return

    user = db.get_user_by_stripe_customer(customer_id)
    if not user:
        return

    sub = db.get_subscription(user["id"])
    if sub:
        db.upsert_subscription(
            user_id=user["id"],
            stripe_customer_id=customer_id,
            stripe_subscription_id=sub.get("stripe_subscription_id"),
            plan=sub["plan"],
            status="past_due",
            device_limit=sub["device_limit"],
            current_period_end=sub.get("current_period_end"),
        )
        log.info("Subscription past_due: user=%d", user["id"])


def _on_subscription_changed(subscription: dict) -> None:
    """Handle subscription update or deletion from Stripe."""
    customer_id = subscription.get("customer")
    if not customer_id:
        return

    user = db.get_user_by_stripe_customer(customer_id)
    if not user:
        log.warning("Subscription event for unknown customer: %s", customer_id)
        return

    stripe_status = subscription.get("status", "")
    # Map Stripe statuses to our statuses
    if stripe_status in ("active", "trialing"):
        status = "active"
    elif stripe_status == "past_due":
        status = "past_due"
    elif stripe_status in ("canceled", "unpaid", "incomplete_expired"):
        status = "canceled"
    else:
        status = "inactive"

    # Determine plan from price
    plan_name = "none"
    device_limit = 0
    items = subscription.get("items", {}).get("data", [])
    if items:
        price_id = items[0].get("price", {}).get("id", "")
        plan_name = _PRICE_TO_PLAN.get(price_id, "none")
        plan_info = PLANS.get(plan_name, {})
        device_limit = plan_info.get("device_limit", 0)

    period_end = None
    if subscription.get("current_period_end"):
        period_end = datetime.fromtimestamp(
            subscription["current_period_end"], tz=timezone.utc
        ).isoformat()

    db.upsert_subscription(
        user_id=user["id"],
        stripe_customer_id=customer_id,
        stripe_subscription_id=subscription.get("id"),
        plan=plan_name,
        status=status,
        device_limit=device_limit,
        current_period_end=period_end,
    )
    log.info("Subscription %s: user=%d plan=%s status=%s",
             subscription.get("id"), user["id"], plan_name, status)
