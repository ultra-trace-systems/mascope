"""
What this server does that an older one did not.

A client can outlive the server it was built against: a File Agent installed
on an instrument PC, a browser tab opened before an update, a frontend image
run against another backend. Before it relies on one of these behaviours, it
checks that the server announces it. ``GET /api/version`` carries the set to
the web app, which reads it at sign-in, and to a paired File Agent, which
reads it with its device token when it starts; the pairing start response
carries it to the agent's setup. An older server announces nothing, which a
client reads as "not supported".

A capability stays for as long as a client may meet a server without it.
"""

SERVER_CAPABILITIES: dict[str, bool] = {
    # An agent's upload is filed under the instrument it reports with it,
    # whatever the file is called.
    "files_uploads_under_reported_instrument": True,
    # A file whose name carries no ionization mode token is accepted: it
    # waits in Raw files for someone to choose its chemistry.
    "files_uploads_without_ionization_token": True,
    # The file list finds an upload by the name it had on the uploading
    # machine (source_filename) among the files registered lately
    # (registered_within), and says how far its processing got - what a File
    # Agent asks about each file it uploaded. An older server ignores both
    # filters and answers with other files.
    "files_listed_by_source_filename": True,
}
