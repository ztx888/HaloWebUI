import copy
import hashlib

import httpx

from open_webui.config import (
    EZFP_PID,
    EZFP_ENDPOINT,
    WEBUI_NAME,
    EZFP_CALLBACK_HOST,
    EZFP_KEY,
    EZFP_AMOUNT_CONTROL,
)
from open_webui.utils.credit.utils import check_amount


class EZFPClient:
    """
    ezfp client
    """

    def get_device_from_ua(self, ua: str) -> str:
        ua = ua.lower()
        if ua.find("micromessenger") != -1:
            return "wechat"
        if ua.find("qq") != -1:
            return "qq"
        if ua.find("alipay") != -1:
            return "alipay"
        if ua.find("android") != -1 or ua.find("iphone") != -1:
            return "mobile"
        return "pc"

    def sign(self, payload: dict) -> dict:
        params = [
            f"{k}={v}"
            for k, v in payload.items()
            if v and k not in ["sign", "sign_type"]
        ]
        params.sort()
        plain_text = "&".join(params) + EZFP_KEY.value
        sign = hashlib.md5(plain_text.encode()).hexdigest()
        payload["sign"] = sign
        payload["sign_type"] = "MD5"
        return payload

    def verify(self, payload: dict) -> bool:
        if payload["pid"] != EZFP_PID.value:
            return False
        payload2 = self.sign(copy.deepcopy(payload))
        return (
            payload["sign"] == payload2["sign"]
            and payload["sign_type"] == payload2["sign_type"]
        )

    async def create_trade(
        self, pay_type: str, out_trade_no: str, amount: float, client_ip: str, ua: str
    ) -> dict:
        # check for amount
        if not check_amount(amount, EZFP_AMOUNT_CONTROL.value):
            return {
                "code": -1,
                "msg": f"amount invalid, allows {' '.join(EZFP_AMOUNT_CONTROL.value.split(','))}",
            }
        # submit
        payload = {
            "pid": int(EZFP_PID.value),
            "type": pay_type,
            "out_trade_no": out_trade_no,
            "notify_url": f"{EZFP_CALLBACK_HOST.value.rstrip('/')}/api/v1/credit/callback",
            "return_url": f"{EZFP_CALLBACK_HOST.value.rstrip('/')}/api/v1/credit/callback/redirect",
            "name": f"{WEBUI_NAME} Credit",
            "money": "%.2f" % amount,
            "clientip": client_ip,
            "device": self.get_device_from_ua(ua=ua),
        }
        payload = self.sign(payload)
        client = httpx.AsyncClient()
        try:
            resp = await client.post(
                url=f"{EZFP_ENDPOINT.value.rstrip('/')}/mapi.php", data=payload
            )
            return resp.json()
        except Exception as err:
            return {"code": -1, "msg": str(err)}
        finally:
            await client.aclose()


ezfp_client = EZFPClient()
