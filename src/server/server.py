import asyncio
import websockets
import socket
import json
import struct

UDP_PORT = 9999
WS_PORT = 8888
DATAGRAM_SIZE = 8

client_to_pos = {}
ws_to_client = {}
udp_socket = None

"""--- Websocket ---"""
async def handle_websocket(websocket):
    ws_to_client[websocket] = None
    print(f"[WS] User connected: {websocket.remote_address}")
    if udp_socket is not None:
        await websocket.send(json.dumps({"event": "udp_request", "ip": udp_socket.getsockname()[0], "port": udp_socket.getsockname()[1]}))
    try:
        async for message in websocket:
            if not isinstance(message, str):
                print(f"[WS] Invalid message type: {type(message)}")
                await websocket.send(json.dumps({"event": "error", "message": "Invalid message type"}))
            else:
                data = json.loads(message)
                if data.get("event") != "udp_response":
                    await websocket.send(json.dumps({"event": "error", "message": "Invalid event type"}))
                else:
                    ip = data.get("ip")
                    port = data.get("port")
                    ws_to_client[websocket] = (ip, port)
                    print(f"[WS] Registered client {websocket.remote_address} with UDP {ip}:{port}")
                    client_to_pos[(ip, port)] = (0, 0)
                    await websocket.send(json.dumps({"event": "connection_information", "number_of_clients": len(ws_to_client)-1}))
                    for ws in list(ws_to_client.keys()):
                        if ws != websocket:
                            await ws.send(json.dumps({"event": "new_connection"}))

    except websockets.exceptions.ConnectionClosed as e:
        print(f"[WS] Connection closed unexpectedly: {e}")
    except Exception as e:
        print(f"[WS] Error handling message: {e}")
    finally:
        print(f"[WS] User disconnected: {websocket.remote_address}")
        if websocket in list(ws_to_client.keys()):
            temp_index = list(ws_to_client.keys()).index(websocket)
            for ws in list(ws_to_client.keys()):
                if list(ws_to_client.keys()).index(ws) < temp_index:
                    index = temp_index - 1
                else:
                    index = temp_index
                disconnect_message = json.dumps({"event": "user_disconnected", "user_index": index})
                if ws != websocket:
                    await ws.send(disconnect_message)
            del_ws = ws_to_client[websocket]
            del ws_to_client[websocket]
            del client_to_pos[del_ws]


async def start_websocket_server():
    server = await websockets.serve(handle_websocket, "127.0.0.1", WS_PORT)
    print(f"[WS] WebSocket server running on ws://127.0.0.1:{WS_PORT}")
    await server.wait_closed()

"""--- UDP ---"""
async def start_udp_server():
    global udp_socket
    loop = asyncio.get_running_loop()
    udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp_socket.bind(("127.0.0.1", UDP_PORT))
    udp_socket.setblocking(False)

    print(f"[UDP] UDP server running on 127.0.0.1:{UDP_PORT}")

    while True:
        data, addr = await loop.sock_recvfrom(udp_socket, DATAGRAM_SIZE)

        if isinstance(data, (bytes, bytearray)) and len(data) == DATAGRAM_SIZE and addr in client_to_pos:
            try:
                new_position = struct.unpack(">II", data)
                client_to_pos[addr] = new_position
                positions = []
                for target_addr, pos in client_to_pos.items():
                    if target_addr != addr:
                        positions.append(pos[0])
                        positions.append(pos[1])
                packed_data = struct.pack(">" + "II" * (len(positions)//2), *positions)
                await loop.sock_sendto(udp_socket, packed_data, addr)
            except Exception as e:
                print(f"[UDP] Error processing datagram from {addr}: {e}")

"""--- Main ---"""
async def main():
    await asyncio.gather(
        start_websocket_server(),
        start_udp_server()
    )

if __name__ == "__main__":
    asyncio.run(main())