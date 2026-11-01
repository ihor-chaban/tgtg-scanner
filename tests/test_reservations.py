from unittest.mock import MagicMock

import pytest

from tgtg_scanner.models.item import Item
from tgtg_scanner.models.reservations import Order, Reservation, Reservations


@pytest.fixture(scope="function")
def reservations():
    mock_client = MagicMock()
    return Reservations(mock_client)


def test_reserve(reservations: Reservations):
    reservations.reserve("123", "Test Item")
    assert len(reservations.reservation_query) == 1


def test_make_orders(reservations: Reservations, tgtg_item: dict):
    callback_mock = MagicMock()
    reservations.reserve("123", "Test Item")
    reservations.make_orders({"123": Item(tgtg_item)}, callback_mock)
    assert len(reservations.active_orders) == 1
    assert len(reservations.reservation_query) == 0
    callback_mock.assert_called_once_with(Reservation("123", 1, "Test Item"))


def test_update_active_orders(reservations: Reservations):
    order = Order("1", "123", 1, "Test Item")
    reservations.client.get_order_status.return_value = {"state": "RESERVED"}  # type: ignore[attr-defined]
    reservations.active_orders = {order.id: order}
    reservations.update_active_orders()
    assert len(reservations.active_orders) == 1
    reservations.client.get_order_status.return_value = {"state": "CANELLED"}  # type: ignore[attr-defined]
    reservations.update_active_orders()
    assert len(reservations.active_orders) == 0


def test_cancel_order(reservations: Reservations):
    order = Order("1", "123", 1, "Test Item")
    reservations.active_orders = {order.id: order}
    reservations.cancel_order(order.id)


def test_cancel_all_orders(reservations: Reservations):
    order1 = Order("1", "123", 1, "Test Item 1")
    order2 = Order("2", "123", 2, "Test Item 2")
    reservations.active_orders = {order1.id: order1, order2.id: order2}
    reservations.cancel_all_orders()


def test_order_now_max_amount(reservations: Reservations):
    reservations.client.create_order.return_value = {"id": "1"}  # type: ignore[attr-defined]
    order = reservations.order_now("123", "Test Item", 3)
    reservations.client.create_order.assert_called_once_with("123", 3)  # type: ignore[attr-defined]
    assert order == Order("1", "123", 3, "Test Item")
    assert reservations.active_orders == {"1": order}


def test_order_now_lowers_amount_on_failure(reservations: Reservations):
    reservations.client.create_order.side_effect = [Exception("limit"), Exception("limit"), {"id": "2"}]  # type: ignore[attr-defined]
    order = reservations.order_now("123", "Test Item", 3)
    assert order.amount == 1
    assert [c.args for c in reservations.client.create_order.call_args_list] == [("123", 3), ("123", 2), ("123", 1)]  # type: ignore[attr-defined]


def test_order_now_all_attempts_fail(reservations: Reservations):
    reservations.client.create_order.side_effect = Exception("sold out")  # type: ignore[attr-defined]
    with pytest.raises(Exception, match="sold out"):
        reservations.order_now("123", "Test Item", 2)
    assert reservations.active_orders == {}
