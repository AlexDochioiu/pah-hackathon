#!/bin/sh
# Push robot/ to the station box (ssh alias nuck9 -> pah@<station ip>); scripts run there from ~/dial.
rsync -av robot/ nuck9:dial/ --exclude '__pycache__'
