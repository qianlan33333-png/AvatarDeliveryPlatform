"""Time-bound AI capability entitlements.

Importing this package registers the capability-owned SQLAlchemy tables on the
shared metadata. Route registration remains the responsibility of the app
composition root.
"""

from backend.app.modules.capabilities.models import (
    CapabilityEntitlement,
    ProductCapabilityMapping,
)

__all__ = ["CapabilityEntitlement", "ProductCapabilityMapping"]
