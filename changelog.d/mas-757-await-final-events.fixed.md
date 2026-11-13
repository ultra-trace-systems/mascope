- **The last results the file converter sends as it stops now reach the
  app.** A peak detection that was running when the converter was told to
  stop, and a conversion that finished while it stopped, sent their result
  just before the converter disconnected, and most such results were lost:
  the peak detection's progress stayed pending, and the upload's success was
  never shown. The converter now waits, up to 5 s, until the backend has
  received every event it sent before it disconnects.
