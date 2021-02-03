import asyncio
import aiorpcx


# Handlers are declared as normal python functions.  aiorpcx automatically checks RPC
# arguments, including named arguments, and returns errors as appropriate
async def handle_echo(message):
    return message

async def handle_sum(*values):
    return sum(values, 0)


handlers = {
    'echo': handle_echo,
    'sum': handle_sum,
}


class ServerSession(aiorpcx.RPCSession):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        print(f'connection from {self.remote_address()}')

    async def connection_lost(self):
        await super().connection_lost()
        print(f'{self.remote_address()} disconnected')

    async def handle_request(self, request):
        handler = handlers.get(request.method)
        coro = aiorpcx.handler_invocation(handler, request)()
        return await coro


loop = asyncio.get_event_loop()
loop.run_until_complete(aiorpcx.serve_rs(ServerSession, 'localhost', 8888))
loop.run_forever()
