# Gmail Auth Setup — Per Team Member

Each PS team member must connect their Gmail once to enable
email-change-intake monitoring for their inbox.

## Steps

1. Ask Dan to set your Email Change Mode on the OC Staff board
   to either Automated or Gatekeeper.
2. On the next skill run you will receive a Monday.com bell notification
   with an activation link.
3. Click the link and sign in with your OpenClinica Google account.
   Read-only access to your Gmail inbox only.
4. Once connected the skill monitors your inbox every 15-30 minutes.

## Mode Options

Automated: Change request emails processed and posted to AI Hub
automatically. You receive a notification when the spec is updated.

Gatekeeper: A review item is created on the Change Requests board for
every detected email. You approve or dismiss before anything is posted.

Off: Your inbox is not monitored.

## Going OOO

Before going on leave switch your mode to Gatekeeper on the OC Staff
board. All inbound change requests surface on the Change Requests board.
A covering colleague can action them without needing your inbox access.

## What the skill reads

Email subject line, plain text body (first 3000 chars), sender name and
address, received timestamp. Does not read attachments, calendar invites,
sent mail, or previously-processed emails.

## Revoking access

Set Email Change Mode to Off on OC Staff board and contact Dan to
revoke the OAuth token from /data/gmail_sessions/.
