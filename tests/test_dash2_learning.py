"""Операция `learning` нового дашборда (R0.2, задача 13): ответ `learning_detail` только своему соединению, недопустимый режим, {ok:false} без разрыва,
повторное сообщение при computing, api_payload('/api/learning'). Временный корень, без реальных state, сети и платформы."""
import asyncio
import json
import shutil
import tempfile
import unittest
from unittest import mock

import _bootstrap  # noqa: F401
from tb.dash import server as dash_server
from tb.dash2 import server as S, topics
from tb.learn import dashapi

try:
    import websockets  # noqa: F401
    from websockets.asyncio.client import connect
    HAVE_WS = True
except ImportError:
    HAVE_WS = False


class LearningTopics(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="tbdash2l_")
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_not_a_polled_topic(self):
        self.assertNotIn("learning", topics.TOPICS)
        self.assertNotIn("learning", topics.MODE_TOPICS)

    def test_detail_empty_root_and_bad_mode(self):
        d = topics.learning_detail(self.root, "evil")
        self.assertTrue(d["ok"])
        self.assertEqual(d["mode"], "paper")

    def test_detail_scrubbed_and_failed_is_ok_false(self):
        with mock.patch.object(dashapi, "detail", return_value={"ok": True, "mode": "paper", "api_key": "SECRET", "x": {"token": "T", "n": 1}}):
            d = topics.learning_detail(self.root, "paper")
        self.assertNotIn("SECRET", json.dumps(d))
        self.assertEqual(d["x"], {"n": 1})
        with mock.patch("tb.learn.report.get", side_effect=RuntimeError("boom")):
            d = topics.learning_detail(self.root, "live")
        self.assertEqual(d, {"ok": False, "mode": "live", "error": "failed"})

    def test_api_payload(self):
        code, obj = dash_server.api_payload("/api/learning", {"mode": ["evil"]}, self.root, None)
        self.assertEqual((code, obj["ok"], obj["mode"]), (200, True, "paper"))
        code, obj = dash_server.api_payload("/api/learning", {"mode": ["all"]}, self.root, None)
        self.assertEqual(code, 200)
        with mock.patch("tb.learn.report.get", side_effect=RuntimeError("boom")):
            code, obj = dash_server.api_payload("/api/learning", {"mode": ["paper"]}, self.root, None)
        self.assertEqual((code, obj["ok"], obj["error"]), (500, False, "failed"))


@unittest.skipUnless(HAVE_WS, "нет websockets")
class LearningServer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        _bootstrap.require_localhost()
        self.root = tempfile.mkdtemp(prefix="tbdash2ls_")
        self.addCleanup(shutil.rmtree, self.root, True)
        self.ready = asyncio.get_event_loop().create_future()
        self.task = asyncio.ensure_future(S.serve(port=0, root=self.root, ready=self._ready))
        await asyncio.wait_for(self.ready, 5)

    def _ready(self, hub, srv):
        self.port = list(srv.sockets)[0].getsockname()[1]
        hub.port = self.port
        self.ready.set_result(True)

    async def asyncTearDown(self):
        self.task.cancel()
        try:
            await self.task
        except BaseException:
            pass

    async def reply(self, ws, timeout=3):
        for _ in range(80):
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if m["topic"] == "learning_detail":
                return m
        self.fail("ответа learning_detail нет")

    async def test_answers_only_asker_and_bad_mode_is_paper(self):
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as a, connect("ws://127.0.0.1:%d/ws" % self.port) as b:
            await a.send(json.dumps({"op": "learning", "mode": {"x": 1}}))
            m = await self.reply(a)
            self.assertTrue(m["data"]["ok"])
            self.assertEqual(m["data"]["mode"], "paper")
            await a.send(json.dumps({"op": "learning", "mode": "evil"}))
            self.assertEqual((await self.reply(a))["data"]["mode"], "paper")
            seen = []
            try:
                while True:
                    seen.append(json.loads(await asyncio.wait_for(b.recv(), 0.5))["topic"])
            except asyncio.TimeoutError:
                pass
            self.assertNotIn("learning_detail", seen)

    async def test_failure_gives_ok_false_and_connection_lives(self):
        async with connect("ws://127.0.0.1:%d/ws" % self.port) as a:
            with mock.patch("tb.learn.report.get", side_effect=RuntimeError("boom")):
                await a.send(json.dumps({"op": "learning", "mode": "paper"}))
                m = await self.reply(a)
            self.assertEqual(m["data"], {"ok": False, "mode": "paper", "error": "failed"})
            await a.send(json.dumps({"op": "ping"}))
            for _ in range(80):
                if json.loads(await asyncio.wait_for(a.recv(), 3))["topic"] == "pong":
                    break
            else:
                self.fail("соединение не отвечает")

    async def test_computing_sends_second_message(self):
        first = {"ok": True, "mode": "paper", "blocks": {"memory_vs_base": {"status": "computing"}}}
        second = {"ok": True, "mode": "paper", "blocks": {"memory_vs_base": {"status": "ok"}}}
        calls = []

        def fake_follow(root, mode, timeout=30, now=None):
            calls.append((mode, timeout))
            return second

        with mock.patch.object(dashapi, "detail", return_value=first), mock.patch.object(dashapi, "is_computing", side_effect=lambda p: p["blocks"]["memory_vs_base"]["status"] == "computing"), \
                mock.patch.object(dashapi, "follow", side_effect=fake_follow):
            async with connect("ws://127.0.0.1:%d/ws" % self.port) as a:
                await a.send(json.dumps({"op": "learning", "mode": "paper"}))
                m1 = await self.reply(a)
                m2 = await self.reply(a)
        self.assertEqual(m1["data"]["blocks"]["memory_vs_base"]["status"], "computing")
        self.assertEqual(m2["data"]["blocks"]["memory_vs_base"]["status"], "ok")
        self.assertEqual(calls[0][0], "paper")

    async def test_computing_follow_none_sends_nothing_more(self):
        first = {"ok": True, "mode": "paper", "blocks": {}}
        with mock.patch.object(dashapi, "detail", return_value=first), mock.patch.object(dashapi, "is_computing", return_value=True), \
                mock.patch.object(dashapi, "follow", return_value=None):
            async with connect("ws://127.0.0.1:%d/ws" % self.port) as a:
                await a.send(json.dumps({"op": "learning", "mode": "paper"}))
                await self.reply(a)
                with self.assertRaises(asyncio.TimeoutError):
                    await self.reply(a, timeout=0.7)


if __name__ == "__main__":
    unittest.main()
