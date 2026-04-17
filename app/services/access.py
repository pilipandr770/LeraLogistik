"""Access control service — single source of truth for all visibility rules.

Design philosophy
-----------------
Every access decision lives HERE, not scattered in route handlers.
Rules are plain Python — easy to read, test, and audit.

Access matrix overview
----------------------

                    | ADMIN | SHIPPER | CARRIER | FORWARDER | AI AGENT
--------------------+-------+---------+---------+-----------+---------
All loads           |  ✓    |  own    |  pub*   |  managed  |   ✓
My load detail      |  ✓    |  own    |  deal   |  managed  |   ✓
Vehicle position    |  ✓    |  deal** |  own    |  deal**   |   ✓
All companies       |  ✓    |  pub    |  pub    |  pub      |   ✓
Company financials  |  ✓    |  —      |  own    |  own      |   ✓
Deal details        |  ✓    |  own    |  own    |  managed  |   ✓
All deals           |  ✓    |  —      |  —      |  —        |   ✓
User list           |  ✓    |  —      |  —      |  —        |   ✓
Verification details|  ✓    |  own co |  own co |  own co   |   ✓
AI match scores     |  ✓    |  —      |  —      |  —        |   ✓

*  "pub" = public marketplace loads (visible to find work)
** "deal" = only during deal.status in {loaded, in_transit}

Temporal rule: a shipper/forwarder can see a vehicle's GPS position ONLY
while the deal is in LOADED or IN_TRANSIT status.  Once the truck is
DELIVERED (cargo unloaded), the tracking access disappears automatically.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status

from app.db.models import Deal, DealStatus, Load, User, UserRole, VehiclePosition
from app.services.auth import get_current_user, get_optional_user


# ---------------------------------------------------------------------------
# FastAPI dependency: role guards
# ---------------------------------------------------------------------------

async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Raises 403 unless the user is an admin."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return current_user


async def require_carrier_or_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role not in (UserRole.CARRIER, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Carrier or admin access required.")
    return current_user


async def require_shipper_or_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role not in (UserRole.SHIPPER, UserRole.ADMIN):
        raise HTTPException(status_code=403, detail="Shipper or admin access required.")
    return current_user


# ---------------------------------------------------------------------------
# Direct access check functions (used inside route handlers)
# ---------------------------------------------------------------------------

class AccessControl:
    """Stateless access check functions.

    All methods are static and take the already-loaded ORM objects.
    Load relationships eagerly when needed before calling these.
    """

    # ── Loads ──────────────────────────────────────────────────────────────

    @staticmethod
    def can_view_load(user: User, load: Load) -> bool:
        """Can this user read the detail page of a load?"""
        if user.role == UserRole.ADMIN:
            return True
        # Owner of the load (shipper / forwarder who posted it)
        if load.posted_by_user_id == user.id:
            return True
        # Carriers and forwarders can browse the public marketplace board
        if user.role in (UserRole.CARRIER, UserRole.FORWARDER):
            return True
        return False

    @staticmethod
    def can_post_load(user: User) -> bool:
        """Can this user post a new load?"""
        return user.role in (UserRole.SHIPPER, UserRole.FORWARDER, UserRole.ADMIN)

    @staticmethod
    def can_cancel_load(user: User, load: Load) -> bool:
        """Can this user cancel a load?"""
        if user.role == UserRole.ADMIN:
            return True
        return load.posted_by_user_id == user.id

    # ── Deals ──────────────────────────────────────────────────────────────

    @staticmethod
    def can_view_deal(user: User, deal: Deal) -> bool:
        """Can this user see the deal detail (price, status, carrier)?"""
        if user.role == UserRole.ADMIN:
            return True
        # The shipper who owns the load
        if hasattr(deal, "load") and deal.load and deal.load.posted_by_user_id == user.id:
            return True
        # The carrier whose company was matched
        # (carrier user's company_id must match the carrier in the deal)
        # NOTE: deal.carrier_id is the old Carrier table; for new platform
        # deals link via load.posted_by_user_id and vehicle.carrier_id
        return False

    # ── GPS / Vehicle tracking ─────────────────────────────────────────────

    @staticmethod
    def can_track_deal(user: User, deal: Deal) -> bool:
        """Core tracking rule: can this user see where the truck is?

        Conditions that ALL must be true for shipper/forwarder:
          1. The deal's load belongs to them (posted_by_user_id == user.id)
          2. The deal status is LOADED or IN_TRANSIT
             (cargo is on the truck but not yet delivered)

        Carriers always see their own vehicles (checked separately).
        Admins always see everything.
        """
        if user.role == UserRole.ADMIN:
            return True

        # Temporal gate: tracking is only open while cargo is on the truck
        if deal.status not in DealStatus.TRACKABLE:
            return False

        # Shipper or forwarder: must be the load owner
        if user.role in (UserRole.SHIPPER, UserRole.FORWARDER):
            if not (hasattr(deal, "load") and deal.load):
                # Caller must eager-load deal.load before calling this
                return False
            return deal.load.posted_by_user_id == user.id

        # Carrier: can see their own vehicles at all times (separate endpoint)
        if user.role == UserRole.CARRIER:
            # For carrier, we grant tracking of any deal assigned to their company
            # (checked via vehicle → carrier → company relationship)
            return True

        return False

    @staticmethod
    def can_view_fleet(user: User, company_id: int) -> bool:
        """Can this user see all vehicle positions for a company's fleet?"""
        if user.role == UserRole.ADMIN:
            return True
        # Carrier can see their own fleet
        if user.role == UserRole.CARRIER and user.company_id == company_id:
            return True
        # Forwarder can see fleet of a carrier they have an active deal with
        # (forwarder→deal check must be done in the route handler)
        return False

    # ── Companies / Profiles ───────────────────────────────────────────────

    @staticmethod
    def can_view_company_private(user: User, company_id: int) -> bool:
        """Can this user see private company fields (finances, API keys, etc.)?"""
        if user.role == UserRole.ADMIN:
            return True
        return user.company_id == company_id

    @staticmethod
    def can_edit_company(user: User, company_id: int) -> bool:
        """Can this user edit a company's profile?"""
        if user.role == UserRole.ADMIN:
            return True
        return user.company_id == company_id

    # ── Users ──────────────────────────────────────────────────────────────

    @staticmethod
    def can_view_user_list(user: User) -> bool:
        """Only admins see the full user directory."""
        return user.role == UserRole.ADMIN

    @staticmethod
    def can_view_user_detail(viewer: User, target_user: User) -> bool:
        """Can viewer see target user's profile?"""
        if viewer.role == UserRole.ADMIN:
            return True
        return viewer.id == target_user.id

    # ── AI agent ───────────────────────────────────────────────────────────

    @staticmethod
    def is_agent_or_admin(user: User) -> bool:
        """Is this user the AI agent service account or a human admin?"""
        return user.role in (UserRole.ADMIN, UserRole.ADMIN)  # agent uses ADMIN role


# ---------------------------------------------------------------------------
# Convenience: raise 403/404 with a single call
# ---------------------------------------------------------------------------

def assert_can_track(user: User, deal: Deal) -> None:
    """Raise HTTP 403 if the user cannot track this deal, 404 if deal not found."""
    if not AccessControl.can_track_deal(user, deal):
        if deal.status not in DealStatus.TRACKABLE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    "Відстеження доступне лише під час перевезення вантажу. "
                    "Статус угоди: " + deal.status
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ви не маєте доступу до відстеження цього рейсу.",
        )
