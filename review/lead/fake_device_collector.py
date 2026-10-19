import sys, json, time, uuid
s = sys.stdout
session = str(uuid.uuid4())
def send(m):
    s.write(json.dumps(m) + "\n"); s.flush()
send({"t":"hello","protocol":1,"seq":0,"session":session,"build":"test","capabilities":["approx","left_s","via","transport"],"devices":[]})
dev = {"key":"logitech:046d:c547:u1","name":"G502 X PLUS","level":77,"approx":"","left_s":None,"charging":False,"online":True,"kind":"mouse","transport":"2.4g","via":"","updated_unix":int(time.time())}
send({"t":"device","session":session,"seq":1,"device":dev})
time.sleep(4)
sys.exit(2)
