"""bank.customers reads."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from minsky_api.store.base import BaseStore
from minsky_api.store.errors import StoreError
from minsky_api.store.models import Customer

_PICK_ATTEMPTS = 5
_RANDOM_CUSTOMER = text(
    """
    select s.customer_id
    from bank.customer_complaint_stats s tablesample bernoulli (0.2)
    where s.is_repeat_complainer is not true
      and exists (
        select 1 from bank.transactions t
        where t.customer_id = s.customer_id
          and t.transaction_date >= :since
          and t.transaction_status = 'Approved'
          and t.amount_usd <= :max_amount
      )
    order by random()
    limit 1
    """
)


class CustomerStore(BaseStore):
    async def get(self, customer_id: str) -> Customer | None:
        return await self._get(Customer, customer_id)

    async def random_with_recent_charge(
        self, *, today: date, window_days: int = 120, max_amount_usd: Decimal = Decimal("500")
    ) -> str | None:
        """A customer who has a posted charge inside the dispute window and under the automatic limit, and is not
        a repeat complainer: the one a demo can take all the way to an opened dispute. Picked at random.

        A sample of the customers, then the check: ordering the 450,000 qualifying charges by random() took seconds
        on the demo VM; this takes a fraction of a second. A sample can come up empty, so it is repeated a few times.
        """
        params = {"since": today - timedelta(days=window_days), "max_amount": max_amount_usd}
        try:
            for _ in range(_PICK_ATTEMPTS):
                result = await self._session.execute(_RANDOM_CUSTOMER, params)
                customer_id = result.scalar()
                if customer_id is not None:
                    return str(customer_id)
        except SQLAlchemyError as error:
            raise StoreError("failed to pick a customer") from error
        return None
