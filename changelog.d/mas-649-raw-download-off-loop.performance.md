- **Downloading one raw file no longer stalls the server.** The copy for a
  single-file download is made beside the server's request handling rather
  than in it, so other pages keep answering while a large raw file is
  prepared. Two downloads of the same file no longer remove each other's
  copy halfway: each is written under a name of its own and then moved into
  place whole.
