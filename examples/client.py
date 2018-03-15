import asyncio
import aiorpcx
import logging

async def main():
    async with aiorpcx.ClientSession('localhost', 8888) as session:
        session.send_request('echo', ["Howdy"])
        session.send_request('sum', [2, 4, "b"])

        for request in session.all_requests():
            try:
                await request
            except Exception:
                print(f"ERROR: {request} -> {request.exception()}")
            else:
                print(f"OK: {request} -> {request.result()}")

        batch = session.new_batch()
        batch.add_request('echo', ["Me again"])
        batch.add_request('sum', list(range(50)))
        session.send_batch(batch)
        await batch
        for request in batch:
            try:
                print(f"OK: {request} -> {request.result()}")
            except Exception:
                print(f"ERROR: {request} -> {request.exception()}")


logging.basicConfig(level=logging.DEBUG)
loop = asyncio.get_event_loop()
loop.run_until_complete(main())
