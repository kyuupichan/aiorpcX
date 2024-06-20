'''Transport implementation for PROXY protocol'''

__all__ = ('serve_pv1rs',)


import asyncio
from functools import partial

from aiorpcx.curio import Queue
from aiorpcx.rawsocket import RSTransport, ConnectionLostError
from aiorpcx.session import SessionKind
from aiorpcx.util import NetAddress


class ProxyProtocolError(Exception):
    pass


class ProxyHeaderData:
    '''Holds data supplied using the PROXY protocol header.

    source and dst may be None or a NetAddress instance'''
    def __init__(self):
        self.source = None
        self.dst = None


class ProxyHeaderV1Data(ProxyHeaderData):
    PROTOCOL_MAGIC = b'PROXY'
    NETWORK_PROTOCOLS = {'TCP4', 'TCP6', 'UNKNOWN'}

    def __init__(self, version, net_proto, source, dst):
        self.verify_version(version)
        self.verify_net_proto(net_proto)
        assert source is None or isinstance(source, NetAddress)
        assert dst is None or isinstance(dst, NetAddress)
        self.version = version
        self.net_proto = net_proto
        self.source = source
        self.dst = dst

    @classmethod
    def verify_version(cls, version):
        if version != 1:
            raise ProxyProtocolError(f"unknown PROXY protocol version {version}")

    @classmethod
    def verify_net_proto(cls, net_proto):
        if net_proto not in cls.NETWORK_PROTOCOLS:
            raise ProxyProtocolError(f"unknown PROXY network protocol {net_proto}")

    @classmethod
    def from_bytes(cls, data):
        header, remainder = data.split(b' ', maxsplit=1)
        assert header == cls.PROTOCOL_MAGIC

        if b' ' in remainder:
            proto, src_ip, dst_ip, src_pt, dst_pt = remainder.split(b' ')
            src = NetAddress(src_ip.decode('ascii'), int(src_pt))
            dst = NetAddress(dst_ip.decode('ascii'), int(dst_pt))
        else:
            proto = remainder
            src = None
            dst = None

        return cls(1, proto.decode('ascii'), src, dst)


class ProxyProtocolV1Processor:
    '''Receives incoming data and separates the PROXY protocol header.'''
    max_size = 107  # maximum frame size for PROXY v1 protocol

    def __init__(self):
        self.queue = Queue()
        self.received_bytes = self.queue.put_nowait
        self.residual = b''

    async def receive_message(self):
        '''Collects bytes until complete PROXY header has been received.'''
        parts = []
        buffer_size = 0
        while True:
            part = self.residual
            self.residual = b''
            new_part = b''
            if not part:
                new_part = await self.queue.get()

            joined = b''.join(parts)
            parts = [joined]
            part = joined + new_part
            npos = part.find(b'\r\n')
            if npos == -1:
                parts.append(new_part)
                buffer_size += len(new_part)
                # Ignore over-sized messages
                if buffer_size <= self.max_size or self.max_size == 0:
                    continue
                raise ProxyProtocolError(f"Expected PROXY v1 protocol header")

            tail, self.residual = new_part[:npos], new_part[npos + 2:]
            parts.append(tail)
            return ProxyHeaderV1Data.from_bytes(b''.join(parts))


class ProxyProtocolMixinBase:
    '''Base class for handling PROXY-wrapped connections.'''
    PROXY_PROCESSOR = None

    def __init__(self, *args, **kwargs):
        self.process_messages = self._process_messages_proxy_init
        self._proxy_processor = self.PROXY_PROCESSOR()
        super().__init__(*args, **kwargs)

    async def _process_messages_proxy_init(self):
        '''Process the inital PROXY protocol header'''
        try:
            excess_bytes = await self._receive_message_proxy_header()
        except (ConnectionLostError, ProxyProtocolError):
            self._closed_event.set()
            await self.session.connection_lost()
        except Exception as e:
            self._closed_event.set()
            await self.session.connection_lost()
            raise e
        else:
            self._proxy_init_done(excess_bytes)

    async def _receive_message_proxy_header(self):
        proxy_data = await self._proxy_processor.receive_message()
        if proxy_data.source is not None:
            self._remote_address = proxy_data.source
        return self._proxy_processor.residual

    def data_received(self, data):
        self._proxy_processor.received_bytes(data)

    def _proxy_init_done(self, excess_bytes):
        '''Enable the underlying protocol handler and re-send extra data
        received by the PROXY protocol handler.'''
        self.data_received = super().data_received
        self.data_received(excess_bytes)
        while not self._proxy_processor.queue.empty():
            self.data_received(self._proxy_processor.queue.get_nowait())
        self.process_messages = super().process_messages
        self._process_messages_task = self.loop.create_task(self.process_messages())
        self._proxy_processor = None


class ProxyProtocolV1Mixin(ProxyProtocolMixinBase):
    PROXY_PROCESSOR = ProxyProtocolV1Processor


class ProxyV1RSTransport(ProxyProtocolV1Mixin, RSTransport):
    pass


async def serve_pv1rs(session_factory, host=None, port=None, *, framer=None, loop=None, **kwargs):
    loop = loop or asyncio.get_event_loop()
    protocol_factory = partial(ProxyV1RSTransport, session_factory, framer, SessionKind.SERVER)
    return await loop.create_server(protocol_factory, host, port, **kwargs)
