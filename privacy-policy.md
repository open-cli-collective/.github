# Privacy Policy

This Privacy Policy applies to the open-source projects maintained by **Open CLI Collective** at [https://github.com/open-cli-collective](https://github.com/open-cli-collective), unless a specific project provides its own privacy policy.

## Data Collection

Except where a project-specific section below says otherwise, Open CLI Collective projects do not operate a hosted service that intentionally collects, stores, sells, or shares personal information.

Some projects may interact with third-party services, APIs, platforms, or hosting providers. Those services may collect information according to their own privacy policies. Open CLI Collective does not control their data practices.

If you interact with our projects through GitHub, GitHub may collect information according to its own privacy policy.

Information you voluntarily provide through issues, discussions, pull requests, or other communications may be publicly visible and may be used as necessary to maintain the projects or respond to you.

## Google CLI (`gro` and `grw`)

This section applies to the unofficial `gro` (Google read-oriented) and `grw` (Google read-write) command-line tools in [open-cli-collective/google-cli](https://github.com/open-cli-collective/google-cli). They are independent open-source software and are not affiliated with, sponsored by, endorsed by, or certified by Google.

### Google account data and scopes

During setup, each tool asks the user to authorize a Google OAuth client. `gro` requests access for Gmail organization, Calendar events, Contacts, the signed-in profile, and Drive metadata and files. Its Gmail scope permits reading and non-destructive organization such as labels, archiving, starring, and read/unread changes; it does not request Gmail settings or full-mail access for permanent deletion. `grw` requests the `gro` access plus Gmail filter management, full Gmail access for its explicitly confirmed permanent-delete path, Calendar event writes, Contacts changes, and full Drive operations. The exact consent screen and granted permissions are controlled by Google and the OAuth client selected by the user.

Commands send requests directly from the user's machine to Google APIs. OAuth authorization and token-refresh exchanges, along with the selected command's queries, account IDs, message or file content, and write or upload data, can be sent to Google as needed for that command. For example, a Gmail send is submitted to Google and Google delivers it to the recipients selected by the user. The project does not provide a hosted proxy or account-data service, and its tools do not send account data to Open CLI Collective. OAuth tokens are read and written through the credential provider selected for that installation; depending on the operating system and configuration, this may be macOS Keychain, Windows Credential Manager, Linux Secret Service, a configured `pass`, `op`, `op-connect`, or `op-desktop` provider, or the optional encrypted-file backend. The selected provider's own privacy and retention practices apply.

### Local storage

The tools keep separate local state for `gro` and `grw`, and each tool can keep multiple named profiles:

- Configuration records the active profile, profile-to-client-file paths, and the OAuth scopes recorded for each profile.
- The OAuth client JSON supplied during setup is stored as a local file (deployment material), including when the tools reuse a client file between the two binaries. It is not the access token store.
- Access and refresh tokens are stored under the tool and profile in the selected credential provider. Tokens are not written to the normal configuration or cache files.
- A verified Google account email and verification time may be stored in the local cache so `profiles list` can identify a profile without another API request.
- `gro` and `grw` may store a disposable shared-Drive metadata cache locally. It is scoped by profile and treated as stale after a fixed 24-hour lifetime; staleness does not automatically delete the file, and the cache can be rebuilt from Google.
- If a user asks either tool to download a Gmail attachment or Drive file, the resulting file is written to the output path selected by the user and remains there until the user deletes it.

The tools do not impose a server-side retention period because Open CLI Collective does not receive this local state. It remains on the user's machine or credential provider until the user removes it. Google retains account data and OAuth grants under Google's policies.

### Removing local data and revoking access

`config clear` removes the OAuth token for the active profile. `config clear --all` also removes that binary's configuration files and local cache, but deliberately leaves the OAuth client JSON and does not remove tokens belonging to other profiles. Select each profile when clearing it. `gro` and `grw` use different configuration directories and credential-provider namespaces, so repeat the cleanup for both binaries when both were used. To remove all local state, delete the remaining client JSON files and credential-provider entries as well.

To stop Google access, revoke the tool's OAuth grant from the Google Account security page (third-party access), then clear the local tokens. Revocation is performed by Google; clearing local state alone does not revoke a grant that already exists at Google.

## Open-Source Software

Open CLI Collective projects are generally distributed under the MIT License. The applicable software license governs your use of each project; this Privacy Policy describes our data practices.

## Changes

We may update this policy if our projects or data practices change.
