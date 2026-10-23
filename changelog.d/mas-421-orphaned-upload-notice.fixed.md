- **An upload the file converter cannot store is no longer lost without
  notice.** A file left in the streams folder across a converter restart has
  no uploader on record, so it cannot be stored and its uploader cannot be
  told. Every active global admin and owner now finds it under "Needs
  attention" in the notifications pane, as "Upload lost" with the file's
  name and why it was set aside, so it can be uploaded again. The server also
  looks through the converter's `failed_files` folder at every start and
  names each file there that has no record, each file once (#2535).
