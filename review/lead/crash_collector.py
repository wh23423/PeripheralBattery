import sys, time, json
out = sys.stdout
out.write(json.dumps({"t":"hello","protocol":1,"session":"crash-session","build":"test","capabilities":[],"devices":[]}) + "\n")
out.flush()
time.sleep(3)
sys.exit(3)
