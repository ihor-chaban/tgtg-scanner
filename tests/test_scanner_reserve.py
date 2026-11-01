from unittest.mock import MagicMock

from tgtg_scanner.errors import TgtgAPIError
from tgtg_scanner.models import Config, Reservations
from tgtg_scanner.models.reservations import Reservation, StartupReservations
from tgtg_scanner.scanner import Scanner


def _scanner(reserve_items: dict[str, int], favorite_ids: list[str], tgtg_item: dict) -> Scanner:
    scanner = Scanner.__new__(Scanner)
    scanner.config = Config()
    scanner.config.reserve_items = reserve_items
    scanner.item_ids = set()
    scanner.location = None
    scanner.tgtg_client = MagicMock()
    scanner.tgtg_client.get_item.return_value = tgtg_item
    scanner.tgtg_client.get_favorites.return_value = [
        {**tgtg_item, "item": {**tgtg_item["item"], "item_id": item_id}} for item_id in favorite_ids
    ]
    scanner.reservations = Reservations(scanner.tgtg_client)
    scanner.metrics = MagicMock()
    scanner.notifiers = MagicMock()
    return scanner


def test_reserve_configured_items(tgtg_item: dict):
    scanner = _scanner({"123": 2}, ["123"], tgtg_item)
    scanner._reserve_configured_items()
    assert scanner.reservations.reservation_query == [Reservation("123", 2, tgtg_item["display_name"])]
    assert scanner.item_ids == set()


def test_reserve_configured_item_not_favorite_is_scanned(tgtg_item: dict):
    scanner = _scanner({"999": 1}, ["123"], tgtg_item)
    scanner._reserve_configured_items()
    assert [r.item_id for r in scanner.reservations.reservation_query] == ["999"]
    assert scanner.item_ids == {"999"}


def test_reserve_configured_item_unknown_is_skipped(tgtg_item: dict):
    scanner = _scanner({"999": 1}, [], tgtg_item)
    scanner.tgtg_client.get_item.side_effect = TgtgAPIError(404, b"not found")
    scanner._reserve_configured_items()
    assert scanner.reservations.reservation_query == []


def test_reserve_configured_items_empty_config_makes_no_requests(tgtg_item: dict):
    scanner = _scanner({}, [], tgtg_item)
    scanner._reserve_configured_items()
    scanner.tgtg_client.get_favorites.assert_not_called()  # type: ignore[attr-defined]


def test_reserve_configured_items_sends_summary_notification(tgtg_item: dict):
    scanner = _scanner({"123": 2, "456": 1}, ["123", "456"], tgtg_item)
    scanner._reserve_configured_items()
    name = tgtg_item["display_name"]
    scanner.notifiers.send.assert_called_once_with(  # type: ignore[union-attr]
        StartupReservations([Reservation("123", 2, name), Reservation("456", 1, name)])
    )


def test_reserve_configured_items_no_notification_when_nothing_reserved(tgtg_item: dict):
    scanner = _scanner({"999": 1}, [], tgtg_item)
    scanner.tgtg_client.get_item.side_effect = TgtgAPIError(404, b"not found")
    scanner._reserve_configured_items()
    scanner.notifiers.send.assert_not_called()  # type: ignore[union-attr]
