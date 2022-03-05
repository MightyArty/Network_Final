import json
import socket
# from venv import logger

from client import Client
from server import Server

import unittest, time, os
from threading import Thread





class Testing(unittest.TestCase):
    """
    End-To-End Testing
    """

    @classmethod
    def setUpClass(cls):
        cls.PORT = 55001
        cls.DEST = "localhost"
        cls.serverSocketM = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cls.serverSocketM.bind(('', 50000))
        cls.server = Server(cls.DEST, cls.PORT, cls.serverSocketM)
        cls.server_thread = Thread(target=cls.server.start)
        cls.server_thread.daemon = True
        cls.server_thread.start()
        #
        cls.clients = []
        users = ['testuser0', 'testuser1', 'testuser2']
        for user in users:
            client = Client(user, cls.DEST, cls.PORT)
            client_thread = Thread(target=client.receive_handler)
            client_thread.daemon = True
            client_thread.start()
            cls.clients.append((client, client_thread))
            client.join()
        time.sleep(1)

    @classmethod
    def tearDownClass(cls):
        cls.server_thread.join(0.1)
        for (client, client_thread) in cls.clients:
            client_thread.join(0.1)

    def test_client_connected_to_server(self):
        self.assertTrue(self.server.users != {})

    def test_client_sends_message_to_another_client(self):
        self.clients[0][0].send_message("testuser2 Hello testuser2!")
        time.sleep(0.1)
        self.assertTrue(
            "forward_message testuser0: Hello testuser2!" in 
            self.clients[2][0].receive_log
        )

    def test_client_sends_message_to_all_clients(self):
        self.clients[0][0].send_message("all_users Hello testuser2!")
        time.sleep(0.1)
        self.assertTrue(
            "forward_message testuser0: Hello testuser2!" in 
            self.clients[2][0].receive_log
        )

    def test_client_get_clients_connected(self):
        self.clients[0][0].send_message_getfiles("show_online")
        time.sleep(0.1)
        self.assertTrue(
            "$testuser2" in self.clients[0][0].receive_log
        )

    def test_client_disconnects_from_server(self):
        self.clients[1][0].logout_user()
        time.sleep(0.1)
        self.assertTrue(len(self.server.users) == 2)


if __name__ == "__main__":
    unittest.main()
