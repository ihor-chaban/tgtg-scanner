import asyncio
import json
import platform
from time import sleep
from unittest.mock import AsyncMock, MagicMock

import pytest
import responses
from pytest_mock.plugin import MockerFixture

from tgtg_scanner.models import Config, Cron, Favorites, Item, Reservations
from tgtg_scanner.models.reservations import Order, OrderRequest
from tgtg_scanner.notifiers.apprise import Apprise
from tgtg_scanner.notifiers.console import Console
from tgtg_scanner.notifiers.discord import Discord
from tgtg_scanner.notifiers.ifttt import IFTTT
from tgtg_scanner.notifiers.ntfy import Ntfy
from tgtg_scanner.notifiers.script import Script
from tgtg_scanner.notifiers.smtp import SMTP
from tgtg_scanner.notifiers.telegram import Telegram
from tgtg_scanner.notifiers.webhook import WebHook

SYS_PLATFORM = platform.system()
IS_WINDOWS = SYS_PLATFORM.lower() in {"windows", "cygwin"}


@pytest.fixture
def reservations() -> Reservations:
    return MagicMock()


@pytest.fixture
def favorites() -> Favorites:
    return MagicMock()


@responses.activate
def test_webhook_json(test_item: Item, reservations: Reservations, favorites: Favorites):
    config = Config()
    config.webhook.enabled = True
    config.webhook.method = "POST"
    config.webhook.url = "https://api.example.com"
    config.webhook.type = "application/json"
    config.webhook.headers = {"Accept": "json"}
    config.webhook.cron = Cron()
    config.webhook.body = (
        '{"content": "${{items_available}} panier(s) disponible(s) à ${{price}} € \nÀ récupérer ${{pickupdate}}'
        '\nhttps://toogoodtogo.com/item/${{item_id}}", "username": "${{display_name}}"}'
    )
    responses.add(responses.POST, "https://api.example.com", status=200)

    webhook = WebHook(config, reservations, favorites)
    webhook.start()
    webhook.send(test_item)
    webhook.stop()

    request = responses.calls[0].request
    body = json.loads(request.body)

    assert request.headers.get("Accept") == "json"
    assert request.headers.get("Content-Type") == "application/json"
    assert body == {
        "content": (
            f"{test_item.items_available} panier(s) disponible(s) à {test_item.price} € \nÀ récupérer {test_item.pickupdate}"
            f"\nhttps://toogoodtogo.com/item/{test_item.item_id}"
        ),
        "username": f"{test_item.display_name}",
    }


@responses.activate
def test_webhook_text(test_item: Item, reservations: Reservations, favorites: Favorites):
    config = Config()
    config.webhook.enabled = True
    config.webhook.method = "POST"
    config.webhook.url = "https://api.example.com"
    config.webhook.type = "text/plain"
    config.webhook.headers = {"Accept": "json"}
    config.webhook.cron = Cron()
    config.webhook.body = (
        "${{items_available}} panier(s) disponible(s) à ${{price}} € \n"
        "À récupérer ${{pickupdate}}\nhttps://toogoodtogo.com/item/${{item_id}}"
    )
    responses.add(responses.POST, "https://api.example.com", status=200)

    webhook = WebHook(config, reservations, favorites)
    webhook.start()
    webhook.send(test_item)
    webhook.stop()

    request = responses.calls[0].request

    assert request.headers.get("Accept") == "json"
    assert request.headers.get("Content-Type") == "text/plain"
    assert request.body.decode("utf-8") == (
        f"{test_item.items_available} panier(s) disponible(s) à {test_item.price} € \n"
        f"À récupérer {test_item.pickupdate}\nhttps://toogoodtogo.com/item/{test_item.item_id}"
    )


@responses.activate
def test_ifttt(test_item: Item, reservations: Reservations, favorites: Favorites):
    config = Config()
    config.ifttt.enabled = True
    config.ifttt.event = "tgtg_notification"
    config.ifttt.key = "secret_key"
    config.ifttt.cron = Cron()
    config.ifttt.body = (
        '{"value1": "${{display_name}}", "value2": ${{items_available}}, '
        '"value3": "https://share.toogoodtogo.com/item/${{item_id}}"}'
    )
    responses.add(
        responses.POST,
        f"https://maker.ifttt.com/trigger/{config.ifttt.event}/with/key/{config.ifttt.key}",
        body="Congratulations! You've fired the tgtg_notification event",
        content_type="text/plain",
        status=200,
    )

    ifttt = IFTTT(config, reservations, favorites)
    ifttt.start()
    ifttt.send(test_item)
    ifttt.stop()

    request = responses.calls[0].request
    body = json.loads(request.body)

    assert request.headers.get("Content-Type") == "application/json"
    assert body == {
        "value1": test_item.display_name,
        "value2": test_item.items_available,
        "value3": f"https://share.toogoodtogo.com/item/{test_item.item_id}",
    }


@responses.activate
def test_ntfy(test_item: Item, reservations: Reservations, favorites: Favorites):
    config = Config()
    config.ntfy.enabled = True
    config.ntfy.server = "https://ntfy.sh"
    config.ntfy.topic = "tgtg_test"
    config.ntfy.title = "New Items - ${{display_name}}"
    config.ntfy.cron = Cron()
    config.ntfy.body = "${{display_name}} - New Amount: ${{items_available}} - https://share.toogoodtogo.com/item/${{item_id}}"
    responses.add(
        responses.POST,
        f"{config.ntfy.server}/{config.ntfy.topic}",
        status=200,
    )

    ntfy = Ntfy(config, reservations, favorites)
    ntfy.start()
    ntfy.send(test_item)
    ntfy.stop()

    request = responses.calls[0].request

    assert request.url == (f"{config.ntfy.server}/{config.ntfy.topic}")
    assert request.headers.get("X-Message").decode("utf-8") == (
        f"{test_item.display_name} - New Amount: {test_item.items_available} - "
        f"https://share.toogoodtogo.com/item/{test_item.item_id}"
    )
    assert request.headers.get("X-Title").decode("utf-8") == (f"New Items - {test_item.display_name}")


@responses.activate
def test_apprise(test_item: Item, reservations: Reservations, favorites: Favorites):
    config = Config()
    config.apprise.enabled = True
    config.apprise.url = "ntfy://tgtg_test"
    config.apprise.title = "New Items - ${{display_name}}"
    config.apprise.cron = Cron()
    config.apprise.body = "${{display_name}} - New Amount: ${{items_available}} - https://share.toogoodtogo.com/item/${{item_id}}"
    responses.add(responses.POST, "https://ntfy.sh/", status=200)

    apprise = Apprise(config, reservations, favorites)
    apprise.start()
    apprise.send(test_item)
    apprise.stop()

    request = responses.calls[0].request
    body = json.loads(request.body)

    assert request.url == "https://ntfy.sh/"
    assert body.get("topic") == "tgtg_test"
    assert body.get("message") == (
        f"{test_item.display_name} - New Amount: {test_item.items_available} - "
        f"https://share.toogoodtogo.com/item/{test_item.item_id}"
    )


def test_console(
    test_item: Item,
    reservations: Reservations,
    favorites: Favorites,
    capsys: pytest.CaptureFixture,
):
    config = Config()
    config.console.enabled = True
    config.console.cron = Cron()
    config.console.body = "${{display_name}} - new amount: ${{items_available}}"

    console = Console(config, reservations, favorites)
    console.start()
    console.send(test_item)
    sleep(0.5)
    captured = capsys.readouterr()
    console.stop()

    assert captured.out.rstrip() == f"{test_item.display_name} - new amount: {test_item.items_available}"


def test_script(
    test_item: Item,
    reservations: Reservations,
    favorites: Favorites,
    capfdbinary: pytest.CaptureFixture,
):
    config = Config()
    config.script.enabled = True
    config.script.cron = Cron()
    config.script.command = "echo ${{display_name}}"

    script = Script(config, reservations, favorites)
    script.start()
    script.send(test_item)
    sleep(0.5)
    captured = capfdbinary.readouterr()
    script.stop()

    encoding = "cp1252" if IS_WINDOWS else "utf-8"
    assert captured.out.decode(encoding).rstrip() == test_item.display_name


def test_smtp(test_item: Item, reservations: Reservations, favorites: Favorites, mocker: MockerFixture):
    mock_SMTP = mocker.MagicMock(name="tgtg_scanner.notifiers.smtp.smtplib.SMTP")
    mocker.patch("tgtg_scanner.notifiers.smtp.smtplib.SMTP", new=mock_SMTP)
    mock_SMTP.return_value.noop.return_value = (250, "OK")

    config = Config()
    config.smtp.enabled = True
    config.smtp.cron = Cron()
    config.smtp.host = "localhost"
    config.smtp.port = 25
    config.smtp.use_tls = False
    config.smtp.use_ssl = False
    config.smtp.sender = "user@example.com"
    config.smtp.recipients = ["user@example.com"]
    config.smtp.subject = "New Magic Bags"
    config.smtp.body = "<b>Á ê</b> </br>Amount: ${{items_available}}"

    smtp = SMTP(config, reservations, favorites)
    smtp.start()
    smtp.send(test_item)
    smtp.stop()

    assert mock_SMTP.call_count == 1
    assert mock_SMTP.return_value.sendmail.call_count == 1
    call_args = mock_SMTP.return_value.sendmail.call_args_list[0][0]
    assert call_args[0] == "user@example.com"
    assert call_args[1] == ["user@example.com"]
    body = call_args[2].split("\n")
    assert body[0].startswith("Content-Type: multipart/alternative;")
    assert body[2] == "From: user@example.com"
    assert body[3] == "To: user@example.com"
    assert body[4] == "Subject: New Magic Bags"
    assert body[8] == 'Content-Type: text/html; charset="utf-8"'
    assert body[12] == f"<b>=C3=81 =C3=AA</b> </br>Amount: {test_item.items_available}"


@pytest.fixture
def mocked_telegram(mocker: MockerFixture):
    mocker.patch(
        "telegram.Bot.get_me",
        return_value=MagicMock(username="test_bot"),
    )
    mocker.patch(
        "telegram.ext.Application.initialize",
        return_value=None,
    )
    mocker.patch(
        "telegram.ext.Updater.start_polling",
        return_value=None,
    )
    mocker.patch(
        "telegram.ext.ExtBot.set_my_commands",
        return_value=True,
    )
    mocker.patch(
        "telegram.ext.Application.start",
        return_value=None,
    )
    mocker.patch(
        "telegram.ext.Updater.stop",
        return_value=None,
    )
    mocker.patch(
        "telegram.ext.Application.stop",
        return_value=None,
    )
    mocker.patch(
        "telegram.ext.Application.shutdown",
        return_value=None,
    )
    mocker.patch(
        "telegram.Bot.send_message",
        return_value=None,
    )
    return mocker


def test_telegram(test_item: Item, reservations: Reservations, favorites: Favorites, mocked_telegram):
    config = Config()
    config.telegram.enabled = True
    config.telegram.token = "1234567890:ABCDEF"
    config.telegram.cron = Cron()
    config.telegram.chat_ids = ["123456"]
    config.telegram.disable_commands = False
    config.telegram.body = "New Magic Bags: ${{items_available}}"
    config.telegram.image = None

    telegram = Telegram(config, reservations, favorites)
    telegram.start()
    telegram.send(test_item)
    sleep(0.5)
    assert telegram.thread.is_alive()
    telegram.stop()
    assert not telegram.thread.is_alive()


@pytest.fixture
def mocked_discord(mocker: MockerFixture):
    mocker.patch(
        "discord.ext.commands.Bot.login",
        return_value=None,
    )
    mocker.patch(
        "discord.ext.commands.Bot.start",
        return_value=None,
    )
    mocker.patch(
        "discord.ext.commands.Bot.command",
        return_value=MagicMock(),
    )
    mocker.patch(
        "discord.ext.commands.Bot.event",
        return_value=MagicMock(),
    )
    mocker.patch(
        "discord.ext.commands.Bot.dispatch",
        return_value=None,
    )
    mocker.patch(
        "aiohttp.BaseConnector.close",
        return_value=None,
    )
    return mocker


def test_discord(test_item: Item, reservations: Reservations, favorites: Favorites, mocked_discord):
    config = Config()
    config.discord.enabled = True
    config.discord.channel = 123456789012345678
    config.discord.token = "ABCDEFGHIJKLMNOPQRSTUVWXYZ.123456.ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKL"

    discord = Discord(config, reservations, favorites)
    discord.start()
    discord.send(test_item)
    sleep(0.5)
    discord.stop()


def _telegram_for_orders(reservations: Reservations, favorites: Favorites) -> Telegram:
    config = Config()
    config.telegram.enabled = False
    config.telegram.chat_ids = ["123456"]
    telegram = Telegram(config, reservations, favorites)
    telegram.chat_ids = ["123456"]
    return telegram


def _callback_update(chat_id: str) -> MagicMock:
    update = MagicMock()
    update.callback_query.message.chat.id = chat_id
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_reply_markup = AsyncMock()
    return update


def test_telegram_order_button_orders_max_amount(reservations: Reservations, favorites: Favorites):
    telegram = _telegram_for_orders(reservations, favorites)
    reservations.order_now.return_value = Order("1", "123", 3, "Test Item")  # type: ignore[attr-defined]
    update = _callback_update("123456")
    asyncio.run(telegram._order_now(update, OrderRequest("123", "Test Item", 3)))
    reservations.order_now.assert_called_once_with("123", "Test Item", 3)  # type: ignore[attr-defined]
    update.callback_query.edit_message_reply_markup.assert_awaited_once_with(reply_markup=None)


def test_telegram_order_button_rejects_unknown_chat(reservations: Reservations, favorites: Favorites):
    telegram = _telegram_for_orders(reservations, favorites)
    update = _callback_update("999")
    asyncio.run(telegram._order_now(update, OrderRequest("123", "Test Item", 3)))
    reservations.order_now.assert_not_called()  # type: ignore[attr-defined]


def test_telegram_order_button_failure(reservations: Reservations, favorites: Favorites):
    telegram = _telegram_for_orders(reservations, favorites)
    reservations.order_now.side_effect = Exception("sold out")  # type: ignore[attr-defined]
    update = _callback_update("123456")
    asyncio.run(telegram._order_now(update, OrderRequest("123", "Test Item", 3)))
    update.callback_query.edit_message_reply_markup.assert_not_awaited()
