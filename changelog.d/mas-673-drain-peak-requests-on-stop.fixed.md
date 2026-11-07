- **A peak detection asked for while the file converter stops now ends in an
  error, not a progress that stays pending.** A stopping converter takes no
  new peak detection request, but stays connected while a conversion in flight
  finishes, and a request the backend sent it in that time was queued and lost
  with the process. Each such request is now answered with an error saying the
  converter stopped, and can be repeated once it is back.
