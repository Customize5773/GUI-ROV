import socket

HOST = "0.0.0.0"
PORT = 5000

server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

server.bind((HOST, PORT))
server.listen(1)

print("================================")
print(" RPI COMMAND SERVER")
print("================================")
print(f"IP   : 192.168.2.2")
print(f"PORT : {PORT}")
print("Menunggu laptop...")
print()

while True:

    conn, addr = server.accept()

    print(f"[CONNECTED] {addr}")

    try:

        while True:

            data = conn.recv(1024)

            if not data:
                break

            command = data.decode().strip()

            print(f"[COMMAND] {command}")

    except Exception as e:

        print("[ERROR]", e)

    finally:

        conn.close()
        print("[DISCONNECTED]")