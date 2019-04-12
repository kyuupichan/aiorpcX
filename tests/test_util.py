import asyncio
from functools import partial

from aiorpcx.util import SignatureInfo, signature_info, is_async_call


async def coro(x, y):
    pass


def test_is_async_call():
    z = coro(2, 3)
    assert not is_async_call(z)
    assert is_async_call(coro)
    assert is_async_call(partial(coro, 3, 4))
    assert is_async_call(partial(partial(coro, 3), 4))
    assert not is_async_call(test_is_async_call)
    assert not is_async_call(partial(is_async_call))
    # Lose a warning
    asyncio.get_event_loop().run_until_complete(z)
