import asyncio
import aiorpcx


async def main(host, port):
    async with aiorpcx.connect_rs(host, port) as session:
        # A good request with standard argument passing
        result = await session.send_request('echo', ["Howdy"])
        print(result)
        # A good request with named argument passing
        result = await session.send_request('echo', {'message': "Hello with a named argument"})
        print(result)

        # aiorpcX transparently handles erroneous calls server-side, returning appropriate
        # errors.  This in turn causes an exception to be raised in the client.
        for bad_args in (
                ['echo'],
                ['echo', {}],
                ['foo'],
                # This causes an error running the server's buggy request handler.
                # aiorpcX catches the problem, returning an 'internal server error' to the
                # client, and continues serving
                ['sum', [2, 4, "b"]]
        ):
            try:
                await session.send_request(*bad_args)
            except Exception as exc:
                print(repr(exc))

        # Batch requests
        async with session.send_batch() as batch:
            batch.add_request('echo', ["Me again"])
            batch.add_notification('ping')
            batch.add_request('sum', list(range(50)))

        for n, result in enumerate(batch.results, start=1):
            print(f'batch result #{n}: {result}')


asyncio.get_event_loop().run_until_complete(main('localhost', 8888))
