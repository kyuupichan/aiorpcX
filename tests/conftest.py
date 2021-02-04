# Pytest looks here for fixtures

import asyncio

import pytest


try:
    import uvloop
    loop_params = (False, True)
except ImportError:
    loop_params = (False, )


# This runs all the tests one with plain asyncio, then again with uvloop
@pytest.fixture(scope="session", autouse=True, params=loop_params)
def use_uvloop(request):
    if request.param:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
