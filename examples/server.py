import asyncio
import aiorpcx
import logging


class MyServerSession(aiorpcx.ServerSession):

    def on_echo(self, arg):
        return arg

    def on_sum(self, *values):
        return sum(values, 0)

    def request_handler(self, method):
        if method == 'sum':
            return self.on_sum
        if method == 'echo':
            return self.on_echo
        return None


async def main():
    server = aiorpcx.Server(MyServerSession, 'localhost', 8888)
    # Serve for 60 seconds
    server.loop.call_later(60, server.close)
    try:
        await server.listen()
        await server.wait_closed()
    finally:
        server.close()


logging.basicConfig(level=logging.DEBUG)
loop = asyncio.get_event_loop()
loop.run_until_complete(main())
