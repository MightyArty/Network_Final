from tkinter import Tk, Label, Button, Text, END
import socket
import sys, json, threading
from threading import Thread
import os, time
import _thread
import random
import logging

# Initialize logger
logging.basicConfig(format='%(asctime)s,%(msecs)03d - %(levelname)s - %(funcName)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S', level=logging.NOTSET)
logger = logging.getLogger()

"""
 server.py has 3 classes i.e 
 1. class Server -> this will listen and handle for new users and commands sent by them
 2. class ServerGUI -> this is for tkinter GUI 
 3. class FTPtoClient -> this will handle file transfer
"""


class FTPtoClient(object):
    def __init__(self, command, serverAddress, filename, MSS):
        self.running = False
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # DGRAM - UDP based protocol
        self.serverAddress = serverAddress
        # print(serverAddress)
        self.file = open(filename, 'rb')  # read binary the file
        self.fileSize = os.path.getsize(filename)  # getting the size of the file through the path
        self.MSS = MSS  # maximum segment size (maximum amount of application layer data in the segment)
        # When sending a large file it breaks the file into chunks of size MSS
        self.SndBufferCapacity = int(65536 / self.MSS)  # the max packet size 64kb
        self.initSeqNum = random.randint(1000, 10000)  # randomly generated sequence number
        self.NextSeqNum = self.initSeqNum
        self.NextByteFill = self.initSeqNum
        self.progress = 1
        self.duplicateAck = 0
        self.rwnd = 0
        self.TimeoutInterval = 1.0
        self.EstimatedRTT = 1.0  # round trip delay - 1.0
        self.DevRTT = 0
        self.congestionStatus = "slow start"
        self.cwnd = MSS
        self.ssthresh = 65536

        self.TimeStart = time.time()  # initialize timer
        # SYN
        # [[SeqNum, Segment, Sent, Start Time]]
        self.SndBuffer = [[
            self.NextByteFill,
            self.toHeader(seqNum=self.NextSeqNum, sf=1) + json.dumps(
                {
                    'command': command,
                    'filename': filename
                }).encode(),
            False,
            5
        ]]
        self.NextByteFill += len(self.SndBuffer[0][1]) - 12
        # Multi-threading
        self.lock = threading.Lock()
        self.pool = [
            threading.Thread(target=f) for f in [
                self.fillSndBuffer, self.rcvAckAndRwnd, self.detectTimeout,
                self.slideWindow
            ]
        ]
        self.first = True

    # starting the thread
    def start(self):
        self.running = True
        for t in self.pool:
            t.start()
        logger.info('Start')

    # sending to header
    def toHeader(self, seqNum=0, ackNum=0, ack=0, sf=0, rwnd=0):
        return seqNum.to_bytes(4, byteorder="little") + ackNum.to_bytes(4, byteorder="little") + ack.to_bytes(1,
                                                                                                              byteorder="little") + sf.to_bytes(
            1, byteorder="little") + rwnd.to_bytes(2, byteorder="little")

    # receiving from header
    def fromHeader(self, segment):
        return int.from_bytes(segment[:4], byteorder="little"), int.from_bytes(segment[4:8],
                                                                               byteorder="little"), int.from_bytes(
            segment[8:9], byteorder="little"), int.from_bytes(segment[9:10], byteorder="little"), int.from_bytes(
            segment[10:12], byteorder="little")

    # read buffer from the file continually
    def fillSndBuffer(self):
        while self.running:
            self.lock.acquire()
            # while the first send is true
            # updating the buffer data
            if self.first == True:
                self.SndBuffer.append([
                    self.NextByteFill,
                    self.toHeader(seqNum=self.NextByteFill) + json.dumps(self.fileSize).encode(),
                    False,
                    time.time()
                ])
                self.first = False
                self.NextByteFill += len(self.SndBuffer[-1][1]) - 12
            # if the file is in the right size
            if len(self.SndBuffer) < self.SndBufferCapacity:
                # reading the file to the size of MSS
                segment = self.file.read(self.MSS)
                if len(segment) == 0:
                    self.file.close()
                    # FIN, '0' is placeholder
                    self.SndBuffer.append([
                        self.NextByteFill,
                        self.toHeader(seqNum=self.NextByteFill, sf=2) + b'0',
                        False
                    ])
                    self.lock.release()
                    break
                # if did not finished to read, update the length to what left
                self.SndBuffer.append([
                    self.NextByteFill,
                    self.toHeader(seqNum=self.NextByteFill) + segment, False,
                    time.time()
                ])
                self.NextByteFill += len(self.SndBuffer[-1][1]) - 12
            self.lock.release()

    # CONGESTION CONTROL, according to graph
    def switchCongestionStatus(self, event):
        oldStatus = self.congestionStatus

        if event == "new ack":
            self.duplicateAck = 0
            if self.congestionStatus == "slow start":
                self.cwnd += self.MSS
            elif self.congestionStatus == "congestion avoidance":
                self.cwnd += self.MSS * (self.MSS / self.cwnd)
            elif self.congestionStatus == "fast recovery":
                self.cwnd = self.ssthresh
                self.congestionStatus = "congestion avoidance"
            else:
                logger.info('congestionStatus error')
                os._exit(0)
        elif event == "time out":
            self.duplicateAck = 0
            self.retransmission()
            if self.congestionStatus == "slow start":
                self.ssthresh = self.cwnd / 2
                self.cwnd = self.MSS
            elif self.congestionStatus == "congestion avoidance":
                self.ssthresh = self.cwnd / 2
                self.cwnd = self.MSS
                self.congestionStatus = "slow start"
            elif self.congestionStatus == "fast recovery":
                self.ssthresh = self.cwnd / 2
                self.cwnd = self.MSS  # It is a problem!
                self.congestionStatus = "slow start"
            else:
                logger.info('congestionStatus error')
                os._exit(0)
        elif event == "duplicate ack":
            self.duplicateAck += 1
            if self.duplicateAck == 3:
                self.retransmission()
                if self.congestionStatus == "slow start":
                    self.ssthresh = self.cwnd / 2
                    self.cwnd = self.ssthresh + 3
                    self.congestionStatus = "congestion avoidance"
                elif self.congestionStatus == "congestion avoidance":
                    self.ssthresh = self.cwnd / 2
                    self.cwnd = self.ssthresh + 3
                    self.congestionStatus = "congestion avoidance"
                elif self.congestionStatus == "fast recovery":
                    pass
                else:
                    logger.info('congestionStatus error')
                    os._exit(0)
        else:
            logger.info('Event type errer : ' + event)
            os._exit(0)
        if self.cwnd >= self.ssthresh:
            self.congestionStatus = "congestion avoidance"
        if oldStatus != self.congestionStatus:
            logging.info("Switch from " + oldStatus + " to " + self.congestionStatus)

    def rcvAckAndRwnd(self):
        while self.running:
            segment = self.socket.recvfrom(self.MSS + 12)[0]
            # logger.info("receive")
            self.lock.acquire()
            _, ackNum, _, _, rwnd = self.fromHeader(segment)
            if ackNum == self.NextSeqNum:
                self.switchCongestionStatus("duplicate ack")
            elif ackNum > self.NextSeqNum:
                self.NextSeqNum = ackNum
                self.switchCongestionStatus("new ack")
                # Show progress
                progress = self.progress
                while (self.NextSeqNum - self.initSeqNum) / self.fileSize >= self.progress * 0.05:
                    self.progress += 1
                if progress < self.progress:
                    logger.info('Sent {0}%'.format((self.progress - 1) * 5))
                    logger.info('EstimatedRTT={0:.2} DevRTT={1:.2} TimeoutInterval={2:.2}'.format(self.EstimatedRTT,
                                                                                                  self.DevRTT,
                                                                                                  self.TimeoutInterval))
                # Cast out from self.SndBuffer
                while len(self.SndBuffer) and self.SndBuffer[0][0] < self.NextSeqNum:
                    # ZYD : get updateTimeoutInterval
                    self.updateTimeoutInterval(self.SndBuffer[0][3])
                    s = self.SndBuffer.pop(0)
                    # Determine whether last cast out is FIN
                    if len(self.SndBuffer) == 0 and self.fromHeader(s[1])[3] == 2:
                        self.running = False
                        self.socket.close()
                        logger.info('Finished')
            self.rwnd = rwnd
            self.TimeStart = time.time()
            self.lock.release()

    # ZYD : Add this for updateTimeoutInterval
    def updateTimeoutInterval(self, startTime):
        endTime = time.time()
        sampleRTT = endTime - startTime
        self.EstimatedRTT = 0.875 * self.EstimatedRTT + 0.125 * sampleRTT
        self.DevRTT = 0.75 * self.DevRTT + 0.25 * abs(sampleRTT - self.EstimatedRTT)
        self.TimeoutInterval = self.EstimatedRTT + 4 * self.DevRTT

    def retransmission(self):
        for segment in self.SndBuffer:
            if segment[0] == self.NextSeqNum:
                segment[3] = time.time()
                self.socket.sendto(segment[1], self.serverAddress)
                logger.info('Sequence number:{0}'.format(self.NextSeqNum))
                self.TimeStart = time.time()
                break

    def detectTimeout(self):
        while self.running:
            self.lock.acquire()
            if time.time() - self.TimeStart > self.TimeoutInterval:
                self.switchCongestionStatus("time out")
            self.lock.release()

    def slideWindow(self):
        while self.running:
            self.lock.acquire()
            for i in range(len(self.SndBuffer)):
                # Flow control
                if self.SndBuffer[i][2] == False and self.SndBuffer[i][
                    0] - self.NextSeqNum <= min(self.rwnd, self.cwnd):
                    # ZYD : package timer start
                    self.SndBuffer[i].append(time.time())
                    self.socket.sendto(self.SndBuffer[i][1],
                                       self.serverAddress)
                    self.TimeStart = time.time()
                    self.SndBuffer[i][2] = True
                elif self.SndBuffer[i][2] == False:
                    break
            self.lock.release()


class ServerGUI:
    def __init__(self, master):
        self.master = master
        master.title("Server")

        self.start_button = Button(master, text="Start", command=self.func_start)
        self.start_button.pack()

        self.text_widget = Text(master, height=20, width=80)
        self.text_widget.pack()

    def func_start(self):
        T.start()
        # S.start()
        msg = "Server started at Address: " + str(DEST) + ", Port: " + str(PORT) + "\n"
        self.start_button["state"] = "disabled"
        self.update_screen(msg)

    def update_screen(self, mssg):
        self.text_widget.insert(END, mssg)
        self.text_widget.see(END)


class Server:
    def __init__(self, dest, port, serverSocketM):
        self.users = {}  # this dictionary will contain user's username and address which are currently logged in with (username, adress_port)
        self.keys = []  # this list will contain just usernames
        self.values = []  # this list will contain just address of users
        self.conn_array = []  # will contain list of tcp connections
        self.server_port = port  # 55001 as declared under variable PORT on start
        self.server_addr = dest  # localhost as declared under variable DEST in start
        self.serverSocketM = serverSocketM  # will be used if client request for file transfer for hand shake
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)  # tcp connection which will handle all clients
        self.sock.bind((self.server_addr, self.server_port))  # binded on port 55001
        self.sock.listen(5)  # will listen for min 5 connections
        self.signal = True  # just used for an while loop
        self.conn, self.client_addr_port = [1, 2]  # initializes variables with dummy input

    def forward_message(self, msg, addr_port_tuple, conn):  # sends other commands or user requested info
        forward_msg = "forward_message" + " " + msg
        conn.send(forward_msg.encode("utf-8"))

    def forward_message1(self, msg, addr_port_tuple, conn):  # sends message from one user to other users
        forward_msg = "forward_message" + " " + msg
        for conn in self.conn_array:
            if str(addr_port_tuple) in str(conn):
                conn1 = conn
                break

        # print (conn1)
        conn1.send(forward_msg.encode("utf-8"))

    # after hand shake in forward_file function, it calls this function in new thread
    # so it makes new udp connection on new port assigned serverPort and starts file transfer
    def userConnection(self, clientAddr, serverPort, file_name):
        # Establish new port
        serverSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # new UDP socket
        s = " "
        msg = "Start a new thread for : " + str(clientAddr) + " at port " + str(serverPort) + "\n"
        my_gui.update_screen(msg)  # updates GUI with the above message
        # Send new port num
        serverSocket.sendto(bytearray(str(serverPort), "utf-8"),
                            clientAddr)  # sends client the new port number that will be used for file transfer
        self.sendFile((clientAddr[0], serverPort),
                      file_name)  # from this sendFile function it calls class FTPtoClient which starts file transfer
        serverSocket.close()  # closes socket after transfering file

    # when request for download file is received in handle_client function
    def forward_file(self, msg, addr_port_tuple):
        file_name = msg

        """
        Trying over and over again until the third hand shake is completed
        and only then establish the connection and throw a message 'file sent successfully'
        """
        rejudge = False
        flag = True
        # waiting for connecting, flag will be set to false when it finishes file transfer and loop will end
        while flag:
            if rejudge == False:
                # receives data from client, for hand shake and connecting to client on new port
                clientData, clientAddr = self.serverSocketM.recvfrom(1024)  # buffer size
                clientData = clientData.decode('utf-8')
            # UDP hand shake, it will connect to client and share new port to
            # client for file transfer in function userConnection
            if clientData == "HAND SHAKE 1":
                logger.info("Receive HAND SHAKE 1")
                # received first hand shake and sending the second handshake to the client address
                self.serverSocketM.sendto(bytearray("HAND SHAKE 2", "utf-8"), clientAddr)
                clientData, clientAddr = self.serverSocketM.recvfrom(1024)
                clientData = clientData.decode('utf-8')
                # if received the third hand shake
                if clientData == "HAND SHAKE 3":
                    logger.info(str(clientAddr) + " has established a connect")
                    rejudge = False
                    LUCKY_PORT = random.choice(
                        PORTS)  # randomly selects a port for udp file transfer from list of ports
                    PORTS.remove(LUCKY_PORT)  # removes selected port from list
                    t = Thread(target=self.userConnection, args=(clientAddr, LUCKY_PORT, file_name))
                    t.start()  # starts new udp connection on selected port
                    while t.is_alive():
                        time.sleep(1)

                    PORTS.append(LUCKY_PORT)  # adds udp port back to list so it can be used by new client
                    flag = 0
                # if the third hand shake did not come then the connection is failed
                else:
                    logger.info(str(clientAddr) + " established a connect failed")
                    rejudge = True
            # if the first hand shake did not come then throw error
            else:
                logger.info("Error establish request:" + str(clientData))
                rejudge = False

        my_gui.update_screen("file sent successfully\n")

    def handle_client(self, conn1, client_addr_port):  # this function handles all client request and process them
        while self.signal:
            try:
                client_message = conn1.recv(1024).decode("utf-8")  # receives request from client
                datalist = client_message.split()
                if len(datalist) >= 1:
                    # if clients sends request to login than it will add user to all 3 lists and updates GUI
                    if datalist[0] == "join":
                        self.users.update({datalist[1]: client_addr_port})
                        self.keys.append(datalist[1])
                        self.values.append(client_addr_port)
                        mssg1 = datalist[1] + " joined the chatroom\n"
                        if 'my_gui' in globals():
                            my_gui.update_screen(mssg1)
                    # if user requests to logout than it remove user from all 3 list i.e keys,values,users
                    elif datalist[0] == "logout":
                        self.users.pop(datalist[1])
                        self.values.remove(self.values[self.keys.index(datalist[1])])
                        self.keys.remove(datalist[1])
                        mssg1 = datalist[1] + " left the chatroom\n"
                        my_gui.update_screen(mssg1)
                        sys.exit()
                    # when client sends message to other user/all users , it calls forward_message1
                    elif datalist[0] == "send_message":
                        sender_client = self.keys[self.values.index(client_addr_port)]
                        client = datalist[1]
                        # if the message is sent to the group chat
                        if client == "all_users":
                            for user in self.users:
                                mssg1 = "msg from:", sender_client, "msg to:", user, "\n"
                                if 'my_gui' in globals():
                                    my_gui.update_screen(mssg1)
                                actual_message = " ".join(datalist[2:])
                                actual_message = sender_client + ": " + actual_message
                                self.forward_message1(actual_message, self.values[self.keys.index(user)], conn1)
                        # if the message is sent to private user
                        else:
                            mssg1 = "msg from:", sender_client, "msg to:", client, "\n"
                            if 'my_gui' in globals():
                                my_gui.update_screen(mssg1)
                            actual_message = " ".join(datalist[2:])
                            actual_message = sender_client + ": " + actual_message
                            self.forward_message1(actual_message, self.values[self.keys.index(client)], conn1)

                    # when client requests list of online users ,
                    # it joins list users with $ , which than is separated by $ in client.py
                    # and prints users which were joined by $ sign
                    elif datalist[0] == "show_online":
                        sender_client = self.keys[self.values.index(client_addr_port)]
                        list_users = self.users
                        actual_message = '$'.join(list_users)
                        self.forward_message(actual_message, client_addr_port, conn1)
                    # when client requests for files on server ,
                    # it gets file in same directory and joins file list with ? sign ,
                    # which will be recognised by client and get files separated by ? sign and prints file list
                    elif datalist[0] == "get_files":
                        sender_client = self.keys[self.values.index(client_addr_port)]
                        list_files = os.listdir()
                        file_tuple = []
                        for file1 in list_files:
                            fileSize = os.path.getsize(file1)
                            file_tuple.append(file1 + "?" + str(fileSize))

                        actual_message = '$'.join(file_tuple)
                        self.forward_message(actual_message, client_addr_port, conn1)

                    # when client request for file download , it calls forward_file function
                    elif datalist[0] == "download_file":
                        sender_client = self.keys[self.values.index(client_addr_port)]
                        file_name = datalist[1]
                        mssg1 = sender_client + " requested to download file " + file_name + "\n"
                        if 'my_gui' in globals():
                            my_gui.update_screen(mssg1)
                        self.forward_message(file_name, client_addr_port, conn1)
                        self.forward_file(file_name, client_addr_port)

                # if length of data list < 1
                else:
                    sender_client = self.keys[self.values.index(client_addr_port)]
                    self.users.pop(sender_client)
                    mssg1 = sender_client + " left the chatroom\n"
                    my_gui.update_screen(mssg1)
                    sys.exit()
            except:
                msg = "Error : " + str(sys.exc_info()) + "\n"
                if 'my_gui' in globals():
                    my_gui.update_screen(msg)
                else:
                    logger.error(msg)
                continue

    # this function is started first and accepts new connections
    # and starts new thread for each client which will keep listening on handle_client function
    def start(self):
        while True:
            conn1, client_addr_port = self.sock.accept()
            c_thread = Thread(target=self.handle_client, args=(conn1, client_addr_port))
            c_thread.start()
            self.conn_array.append(conn1)

    # send the message and call the FTPtoClient class
    def sendFile(self, serverAddress, filename):
        time.sleep(2)
        client = FTPtoClient("send", serverAddress, filename, 5360)
        client.start()


if __name__ == "__main__":
    PORT = 55001  # tcp server will keep listening on this port
    DEST = "localhost"
    # ports which will be used for udp file transfer
    PORTS = [55002, 55003, 55004, 55005, 55006, 55007, 55008, 55009, 55010, 55011, 55012, 55013, 55014, 55015]
    root = Tk()  # initialises tkinter root
    # declares ServerGUI class to my_gui variable ,
    # which can be called from other class, like GUI elements can be called and updated from other classes
    my_gui = ServerGUI(root)
    # when user downloads file, client will hand shake with server on this socket
    # on port 50000 which is fixed on client.py and after that
    # new port will be assigned to that client for file transfer
    serverSocketM = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    serverSocketM.bind(('', 50000))  # udp bind on this port for hand shake
    logger.info("UDP listening on " + str(50000))

    # TCP server starts from here
    S = Server(DEST, PORT, serverSocketM)
    # keeps on listening for new connections over tcp and append new connections to list
    T = Thread(target=S.start)
    T.daemon = True

    try:
        root.mainloop()  # tkinter mainloop starts
    except (KeyboardInterrupt, SystemExit):
        exit()
