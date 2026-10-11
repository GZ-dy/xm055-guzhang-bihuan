from __future__ import annotations

import unittest

from scripts.wecom_app_callback import WeComAppCrypt, _plain_text_reply, _sha1_signature


class WeComAppCallbackTest(unittest.TestCase):
    def test_encrypt_decrypt_round_trip(self) -> None:
        token = "token"
        corp_id = "ww1234567890abcdef"
        aes_key = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
        crypt = WeComAppCrypt(token, aes_key, corp_id)
        plain = (
            "<xml><ToUserName><![CDATA[ww1234567890abcdef]]></ToUserName>"
            "<FromUserName><![CDATA[user1]]></FromUserName>"
            "<MsgType><![CDATA[text]]></MsgType>"
            "<Content><![CDATA[BOMC test]]></Content></xml>"
        )

        encrypted_reply = crypt.encrypt_reply(plain, "nonce", "123")
        encrypt = encrypted_reply.split("<Encrypt><![CDATA[", 1)[1].split("]]></Encrypt>", 1)[0]
        signature = _sha1_signature(token, "123", "nonce", encrypt)
        post_data = f"<xml><Encrypt><![CDATA[{encrypt}]]></Encrypt></xml>".encode("utf-8")

        self.assertEqual(crypt.decrypt_message(post_data, signature, "123", "nonce"), plain)

    def test_plain_text_reply_swaps_sender_and_receiver(self) -> None:
        incoming = (
            "<xml><ToUserName><![CDATA[corp]]></ToUserName>"
            "<FromUserName><![CDATA[user]]></FromUserName>"
            "<MsgType><![CDATA[text]]></MsgType></xml>"
        )

        reply = _plain_text_reply(incoming, "ok")

        self.assertIn("<ToUserName><![CDATA[user]]></ToUserName>", reply)
        self.assertIn("<FromUserName><![CDATA[corp]]></FromUserName>", reply)
        self.assertIn("<Content><![CDATA[ok]]></Content>", reply)


if __name__ == "__main__":
    unittest.main()
