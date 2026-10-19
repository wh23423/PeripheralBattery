import sys, time, json
out = sys.stdout
out.write(json.dumps({"t":"hello","protocol":1,"session":"hang-session","build":"test","capabilities":[],"devices":[]}) + "\n")
out.flush()
# deliberately ignore stdin/shutdown and never exit
while True:
    time.sleep(1)
