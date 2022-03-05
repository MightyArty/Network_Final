from tkinter import Tk, Label, Button, Text, END, LEFT, N, W, E, NW, ttk, INSERT
import socket
import sys, time, os, json
import random
from threading import Thread
import getopt
from socket import timeout
# from socket import IPPROTO_TCP, SO_KEEPALIVE, TCP_KEEPALIVE, TCP_KEEPINTVL, TCP_KEEPCNT
import logging

# Initialize logger
logging.basicConfig(format='%(asctime)s,%(msecs)03d - %(levelname)s - %(funcName)s - %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S', level=logging.NOTSET)
logger = logging.getLogger()

"""
 client.py has 3 classes ->
 1. class FTPtoServer -> this download file from server
 2. class ServerSocket -> after successfully hand shake , this class is called which starts receiving file data
 3. class ClientGUI -> GUI part is initialized from this class and all elements are declared
 4. class Client -> when client logins , this class is called and receive_handler keeps on listening messages from server 
    and it processes those messages
"""

# header structure
def toHeader(seqNum=0, ackNum=0, ack=0, sf=0, rwnd=0):
    return seqNum.to_bytes(4, byteorder="little") + ackNum.to_bytes(4, byteorder="little") + ack.to_bytes(1,
                                                                                                          byteorder="little") + sf.to_bytes(
        1, byteorder="little") + rwnd.to_bytes(2, byteorder="little")


def fromHeader(segment):
    return int.from_bytes(segment[:4], byteorder="little"), int.from_bytes(segment[4:8],
                                                                           byteorder="little"), int.from_bytes(
        segment[8:9], byteorder="little"), int.from_bytes(segment[9:10], byteorder="little"), int.from_bytes(
        segment[10:12], byteorder="little")


# Download the file from the server
class FTPtoServer(object):
    def __init__(self, clientAddress, filename, MSS, progressbar, text_widget):
        self.finished = False
        self.progressbar = progressbar
        self.text_widget = text_widget
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # DGRAM - UDP based protocol
        self.clientAddress = clientAddress
        self.filename = filename
        # MSS: max segment size the sender will accept
        self.MSS = MSS
        self.RcvBufferCapacity = int(65536 / self.MSS)  # the max packet size 64kb
        self.rwnd = 0
        self.RcvBuffer = []
        self.first = True
        self.fileSize = 0
        self.progress = 1
        self.count = 0
        self.lastTime = 0
        self.last_bytes = 0

    def rcvSegment(self, segment):
        seqNum, _, _, sf, _ = fromHeader(segment)
        data = segment[12:]

        finishFlag = False
        # sf = 1 -> SYN
        # SYN: used for connection setup
        if sf == 1:
            info = json.loads(data.decode())
            self.file = open(self.filename, 'wb')
            self.NextSeqNum = seqNum + len(data)
        # sf = 2 -> FIN
        # FIN: used for connection termination
        elif len(self.RcvBuffer) < self.RcvBufferCapacity and seqNum >= self.NextSeqNum:
            if self.first == True:
                self.fileSize = json.loads(data.decode())
                self.first = False
                self.NextSeqNum = seqNum + len(data)
                self.lastTime = time.time()
            else:
                # Show speed
                progress = self.progress
                tempFileName = self.filename.split('/')[-1]
                while self.count * self.MSS / self.fileSize >= self.progress * 0.05:
                    self.progress += 1
                if progress < self.progress:
                    self.progressbar["value"] = (self.progress - 1) * 5
                    speed = self.count * self.MSS / (time.time() - self.lastTime)

                i = 0
                while i < len(self.RcvBuffer) and self.RcvBuffer[i][0] < seqNum:
                    i += 1
                # Determine whether duplicate
                if len(self.RcvBuffer) == 0 or i == len(self.RcvBuffer) or (self.RcvBuffer[i][0] != seqNum):
                    self.RcvBuffer.insert(i, (seqNum, data, sf))
                    # Cast out from self.RcvBuffer
                    i = 0
                    while i < len(self.RcvBuffer) and self.NextSeqNum == self.RcvBuffer[i][0]:
                        self.NextSeqNum += len(self.RcvBuffer[i][1])
                        # FIN
                        if self.RcvBuffer[i][2] == 2:
                            self.file.close()
                            finishFlag = True
                            str2 = "Last byte is " + str(self.RcvBuffer[i][0])[-3:] + "\n"
                            self.text_widget.insert(END, str2)
                        else:
                            self.file.write(self.RcvBuffer[i][1])
                            self.count += 1
                            # self.last_bytes = self.RcvBuffer[i][1]
                            # print (self.last_bytes)
                        i += 1
                    self.RcvBuffer = self.RcvBuffer[i:]

                    if len(self.RcvBuffer) == self.RcvBufferCapacity:
                        self.RcvBuffer.pop(0)
        # ACK
        self.socket.sendto(
            toHeader(ackNum=self.NextSeqNum, ack=1, rwnd=(self.RcvBufferCapacity - len(self.RcvBuffer)) * self.MSS),
            self.clientAddress)
        return finishFlag


# after hand shake start receiving data
class ServerSocket(object):
    def __init__(self, serverPort, MSS, progressbar, text_widget):
        self.serverPort = serverPort
        self.MSS = MSS
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)  # udp
        self.connections = {}  # {clientAddress: FTPtoServer}
        self.progressbar = progressbar
        self.text_widget = text_widget

    def start(self, filename):
        self.socket.bind(('', self.serverPort))
        self.listen(filename)

    def listen(self, filename):
        while True:
            segment, clientAddress = self.socket.recvfrom(self.MSS + 12)
            for c in list(self.connections.items()):
                if c[1].finished:
                    del (self.connections[c[0]])
            if clientAddress not in self.connections:
                self.connections[clientAddress] = FTPtoServer(clientAddress, filename, self.MSS, self.progressbar,
                                                              self.text_widget)
            if self.connections[clientAddress].rcvSegment(segment):
                return


# this function is called after hand shake with server is done and it than calls class ServerSocket which will start
# downloading
def getFile(PORT, filename, progressbar, text_widget):  # calls class ServerSocket
    server = ServerSocket(PORT, 5360, progressbar, text_widget)
    server.start(filename)


# this class initializes GUI elements and defines function/commands which will be called on pressing those buttons on
# GUI
class ClientGUI:
    def __init__(self, master):
        self.master = master
        master.title("Client")
        self.file_sizes = {}

        self.master.geometry("700x550")
        self.master.resizable(0, 0)
        self.master.columnconfigure(0, weight=1)
        self.master.columnconfigure(1, weight=1)
        self.master.columnconfigure(2, weight=1)
        self.master.columnconfigure(3, weight=1)
        self.master.columnconfigure(4, weight=1)
        self.master.rowconfigure(0, weight=5)
        self.master.rowconfigure(1, weight=5)
        self.master.rowconfigure(2, weight=5)
        self.master.rowconfigure(3, weight=5)

        #############################
        self.login_button = Button(master, text="Login", command=self.func_login)
        # self.greet_button.pack(side=LEFT, padx=5, pady=5)
        self.login_button.grid(row=0, column=0, sticky=NW, pady=2, padx=5, ipadx=40, ipady=5)

        self.textbox_username = Text(master, height=2, width=20)
        # self.textbox_username.pack(side=LEFT, padx=5, pady=5)
        self.textbox_username.grid(row=0, column=1, sticky=NW, pady=2, padx=5, ipadx=50, ipady=5)
        self.textbox_username.insert(INSERT, "username")

        self.textbox_address = Text(master, height=2, width=20)
        self.textbox_address.grid(row=0, column=2, sticky=NW, pady=2, padx=5, ipadx=90, ipady=5)
        self.textbox_address.insert(INSERT, "localhost")

        self.showonline_button = Button(master, text="Show Online", state="disabled", command=self.show_online)
        self.showonline_button.grid(row=0, column=3, sticky=NW, pady=2, padx=5, ipadx=100, ipady=5)

        self.clear_button = Button(master, text="Clear", state="disabled", command=self.clear_logout)
        self.clear_button.grid(row=0, column=4, sticky=NW, pady=2, padx=5, ipadx=110, ipady=5)

        ################################

        self.showfiles_button = Button(master, text="Show Server Files", state="disabled", command=self.get_files)
        self.showfiles_button.grid(row=1, column=0, sticky=W, pady=2, padx=5, ipadx=30, ipady=10)

        self.text_widget = Text(master, height=20, width=80)
        self.text_widget.grid(row=2, column=0, sticky=W, pady=2, padx=5, columnspan=5)

        self.label1 = Label(master, text="To (blank to all)")
        self.label1.grid(row=3, column=0, sticky=W, pady=2, padx=5, ipadx=80, ipady=5)
        self.textbox_to = Text(master, height=2, width=20)
        self.textbox_to.grid(row=4, column=0, sticky=W, pady=2, padx=5, ipadx=80, ipady=5)

        self.label2 = Label(master, text="Message")
        self.label2.grid(row=3, column=1, sticky=W, pady=2, padx=5, ipadx=80, ipady=5)
        self.textbox_msg = Text(master, height=2, width=30)
        self.textbox_msg.grid(row=4, column=1, sticky=W, pady=2, padx=5, ipadx=80, ipady=5)

        self.send_button = Button(master, text="Send", state="disabled", command=self.func_send)
        self.send_button.grid(row=4, column=2, sticky=W, pady=2, padx=5)

        self.textbox_serverfile = Text(master, height=2, width=30)
        self.textbox_serverfile.grid(row=5, column=0, sticky=W, pady=5, padx=5, ipadx=80, ipady=5)
        self.textbox_serverfile.insert(INSERT, "Enter Server File Name")

        self.textbox_clientfile = Text(master, height=2, width=30)
        self.textbox_clientfile.grid(row=5, column=1, sticky=W, pady=5, padx=5, ipadx=80, ipady=5)
        self.textbox_clientfile.insert(INSERT, "Enter File Name")

        self.download_button = Button(master, text="Download", state="disabled", command=self.download_file1)
        self.download_button.grid(row=5, column=2, sticky=W, pady=5, padx=5)

        s = ttk.Style()
        s.theme_use('clam')
        s.configure("red.Horizontal.TProgressbar", troughcolor='green', background='red')
        self.progress = ttk.Progressbar(master, style="red.Horizontal.TProgressbar", orient="horizontal", length=400,
                                        mode="determinate")
        self.progress.grid(row=6, column=0, sticky=W, pady=2)

        self.bytes = 0
        self.maxbytes = 0

    # when pressed login it will call Client class
    # and runs receive_handle function in new thread
    # and inactivate login button which will keep on listening for new messages from server on tcp
    def func_login(self):
        self.login_button["state"] = "disabled"
        USER_NAME = self.textbox_username.get("1.0", END)
        self.S = Client(USER_NAME, DEST, PORT)
        self.T = Thread(target=self.S.receive_handler)
        self.T.daemon = True
        self.T.start()
        self.S.join()
        self.showonline_button["state"] = "active"
        self.clear_button["state"] = "active"
        self.showfiles_button["state"] = "active"
        self.send_button["state"] = "active"
        self.download_button["state"] = "active"
        msg = "Client logged in as Username: " + USER_NAME.strip() + " on Address: " + str(DEST) + ", Port: " + str(
            PORT) + "\n"
        self.text_widget.insert(END, msg)
        self.text_widget.see(END)

    # when logging out just change all the buttons to 'disabled'
    def clear_logout(self):  # when clicked on logout, calls function logout_user in Client class
        self.login_button["state"] = "active"
        self.text_widget.delete('1.0', END)
        self.text_widget.see(END)
        self.showonline_button["state"] = "disabled"
        self.clear_button["state"] = "disabled"
        self.showfiles_button["state"] = "disabled"
        self.send_button["state"] = "disabled"
        self.download_button["state"] = "disabled"
        self.S.logout_user()

    def func_send(self):
        if len(self.textbox_to.get("1.0", END)) == 1:
            userinput = "msg all_users " + self.textbox_msg.get("1.0", END)
        else:
            userinput = "msg " + self.textbox_to.get("1.0", END) + self.textbox_msg.get("1.0", END)
        input_recv = userinput.split()
        if input_recv[0] == "msg":
            self.S.send_message(" ".join(input_recv[1:]))
        else:
            print("incorrect user input format")

    def get_files(self):
        userinput = "get_files"
        self.S.send_message_getfiles(userinput)

    def show_online(self):
        userinput = "show_online"
        self.S.send_message_getfiles(userinput)

    def download_file1(self):
        t = Thread(target=self.download_file, args=(), daemon=True)
        t.start()

    def download_file(self):
        DEST_IP = "127.0.0.1"
        DEST_PORT = 50000  # port as defined on server.py for listening to udp messages
        userinput = "download_file " + self.textbox_serverfile.get("1.0", END)
        file_name = self.textbox_clientfile.get("1.0", END)  # gets file name requested from GUI
        file_name = file_name.strip()
        self.text_widget.insert(END, "Got a download Connection\n")
        self.text_widget.see(END)

        PORT1 = random.randint(11000, 11099)  # selects random port for just hand shaking on udp
        DEST1 = "localhost"
        self.S.send_message_getfiles(userinput)  # sends file name to be downloaded to server
        buf = 1024

        f_nm1 = self.textbox_serverfile.get("1.0", END).strip()  # name of file
        f_sz1 = self.file_sizes[f_nm1].strip()  # size of file
        self.progress["value"] = 0  # defines max and min value of progressbar
        self.progress["maximum"] = 100
        i = 0

        # init UDP
        clientSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        clientSocket.bind((DEST1, PORT1))
        # TCP handshake
        clientSocket.settimeout(4)
        clientSocket.sendto(bytearray("HAND SHAKE 1", "utf-8"), (DEST_IP, DEST_PORT))
        # get server data and address from pkt
        try:
            serverData, serverAddr = clientSocket.recvfrom(1024)
            serverData = serverData.decode('utf-8')
        except:
            clientSocket.sendto(bytearray("HAND SHAKE 1", "utf-8"), (DEST_IP, DEST_PORT))
            serverData, serverAddr = clientSocket.recvfrom(1024)
            serverData = serverData.decode('utf-8')
        # resolve server data
        if serverData == "HAND SHAKE 2":
            clientSocket.sendto(bytearray("HAND SHAKE 3", "utf-8"), (DEST_IP, DEST_PORT))
        else:
            # exit on error
            logger.info("Connection failed!")
            exit(0)
        logger.info("Connect to the server successfully!")
        self.text_widget.insert(END, "Connect to the server successfully!\n")
        self.text_widget.see(END)

        # Get new port
        serverData, serverAddr = clientSocket.recvfrom(1024)
        serverPort = int(serverData.decode('utf-8'))
        logger.info("Transfer port is " + str(serverPort))

        # starts new thread after getting new port from server for receiving file from that port
        t = Thread(target=getFile, args=(serverPort, file_name, self.progress, self.text_widget), daemon=True)
        t.start()
        while t.is_alive():
            time.sleep(1)

        clientSocket.close()
        self.text_widget.insert(END, "User Received 100% out of file\n")
        self.text_widget.see(END)

    def update_screen(self, mssg):
        datalist = mssg.split("$")
        self.text_widget.insert(END, "---list starts---\n")
        if len(datalist) > 1:  # checks if it is list of users or not , else it will print message from server as it is
            for data in datalist:
                if "?" in data:  # is server sends list of files it will contain ? , so it separates file name in d1
                    # list and file size in file_sizes list
                    d1 = data.split("?")[0] + "\n"
                    self.text_widget.insert(END, d1)
                    self.file_sizes.update({data.split("?")[0]: data.split("?")[1]})
                else:  # prints list of users
                    d1 = data + "\n"
                    self.text_widget.insert(END, d1)
        else:  # prints message from server as it is
            self.text_widget.insert(END, mssg)
        self.text_widget.insert(END, "---list ends---\n")
        self.text_widget.see(END)  # scrolls down to last line on GUI textbox


class Client:
    def __init__(self, username, dest, port):
        self.server_addr = dest  # localhost
        self.server_port = port  # 55001
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)   # tcp
        self.sock.connect((self.server_addr, self.server_port))  # starts tcp connection to server
        self.name = username
        self.receive_log = ""

    def join(self):  # sends message to server for login
        join_message = "join" + " " + self.name
        self.sock.sendall(join_message.encode("utf-8"))

    def logout_user(self):  # sends message to server for logout
        logout_message = "logout" + " " + self.name
        self.sock.sendall(logout_message.encode("utf-8"))
        self.sock.close()

    def send_message(self, msg):  # this function is called when client sends message to other user/users
        actual_message = "send_message" + " " + msg
        self.sock.sendall(actual_message.encode("utf-8"))

    def send_message_getfiles(self, msg):  # gets list of files on server
        actual_message = msg
        self.sock.sendall(actual_message.encode("utf-8"))

    # this function keeps on listening for new messages on tcp
    # from server and shows info received from server on GUI textbox
    def receive_handler(self):
        while True:
            try:
                server_message = self.sock.recv(1024).decode("utf-8")
                self.receive_log += server_message + "\n"
                datalist = server_message.split()

                if datalist[0] == "forward_message":
                    msg_recv_list = datalist[1:]
                    msg_recv = " ".join(msg_recv_list)
                    msg_recv = msg_recv + "\n"
                    if 'my_gui' in globals():
                        my_gui.update_screen(msg_recv)
            except:
                msg = "Error : " + str(sys.exc_info()) + "\n"
                if 'my_gui' in globals():
                    my_gui.update_screen(msg)
                else:
                    logging.error(msg)
                continue


if __name__ == "__main__":
    DEST = 'localhost'
    PORT = 55001  # this port is used for tcp connection
    DEST_IP = "127.0.0.1"
    DEST_PORT = 50000  # this port is used for hand shaking on udp before starting download file

    root = Tk()
    my_gui = ClientGUI(root)  # initializes tkinter class

    try:
        root.mainloop()  # starts tkinter mainloop
    except (KeyboardInterrupt, SystemExit):
        sys.exit()
