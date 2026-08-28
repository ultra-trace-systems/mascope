# First steps in the app

This page is for the first time you open Mascope — typically at an address your
organisation runs, with an account an administrator created for you. It walks
you from signing in to having a place ready for your own data.

!!! tip "No Mascope to log in to yet?"
    [Run the demo](index.md) instead — one Docker command gives you a local
    Mascope preloaded with real data to click around in.

## Sign in

Open your Mascope address and enter your **Email** and **Password**, then click
**Login**. There is no self-service signup or password reset — accounts are
created by an administrator, so if you are missing yours (or a password reset),
ask them.

If your account has [two-factor authentication](../guides/two-factor.md)
turned on, signing in has a second step: enter the six-digit code from your
authenticator app (or one of your saved recovery codes). On a server that
*requires* two-factor authentication for your role, Mascope instead walks you
through setting it up right after your first sign-in.

### If Mascope asks you to set a new password

Right after signing in you may land on **Set a new password** instead of the
dashboard. That happens when your account was created with a password someone
else chose, when an administrator issued you a temporary one, or when an
administrator has asked everyone to set a new password. It is not a sign that
anything is wrong, and the screen itself says which of the three applies.

Enter your current password, then choose a new one. It must be at least 12
characters, must differ from your current password, and cannot be a commonly used
password or contain your email address or user name. The rest of Mascope stays
unavailable until you set it.

One thing to know: changing your password replaces your API access tokens. If you
use Mascope from a Jupyter notebook or the SDK, or you have paired an instrument
agent, you will need to generate a new token or pair again afterwards.

## The dashboard at a glance

After signing in you land on the dashboard. Everything happens on this one
screen:

- **Top bar** — on the left, a house icon (**Home menu**) and a breadcrumb
  showing where you are (workspace › dataset › batch). On the right, chips for
  the active filters, such as the selected samples or target ions.
- **Left panel** — the *sample browser*: drill from datasets into batches into
  samples. Below it, the *match browser* lists the focused batch's target
  collections and their matched ions.
- **Right panel** — the working views, as tabs: **Raw files**, **Batch**,
  **Sample**, and **Match**. Tabs enable as their subject exists — **Batch**
  needs a batch with samples, **Sample** a selected sample, **Match** a
  visualized ion match.
- **Bottom edge** — a thin progress strip; each running server task (upload,
  processing, matching) shows as a colored bar with a tooltip.

Two buttons sit below the tab row on the right: the question mark toggles
**help mode** (also `Alt`+`H`) — hover anything while it is on and Mascope
explains what you are looking at — and the book opens this documentation.

Mascope remembers where you were: reloading the page restores your selection.
To show a colleague exactly what you see, use **Copy link to this view** in the
top bar and send them the link.

## Pick or create a workspace

Everything you analyse lives in a [workspace](../concepts/index.md#workspaces-and-sharing).
On first login, the **Home menu** drawer opens by itself and asks you to pick
one.

If your team already has a workspace, select it and you are done. To create
your own, click **Create** at the top of the *Workspaces* list, give it a name
and description, and click **Create**. You become its owner and can invite
members later (right-click the workspace → **Manage members**).

The Home menu is also where **Notifications** (a log of past toasts) and
**Settings** live — the Settings tab has your account details, **Change
password**, **[Two-factor authentication](../guides/two-factor.md)**, the
light/dark **Theme** toggle, and **API Access Tokens** for the
[SDK](../sdk/index.md) and the [File Agent](../instruments/index.md#the-file-agent).

## Create a dataset and a batch

With a workspace selected, the left panel shows its **Datasets**. Data is
organised as workspace → dataset → batch → samples — the
[data hierarchy](../concepts/index.md#the-data-hierarchy) explains each level.

1. Click **Create dataset** (the plus button at the top of the *Datasets*
   pane), name it after your study or campaign, and click **Create**.
2. Click the new dataset to open it, then click **Create batch** (the plus
   button in the *Batches* pane), name it, and click **Save**. A batch holds
   the samples you will analyse and compare together.
3. Click the batch to open it. The *Samples* pane is empty — time to get data
   in.

To go back up a level, use the breadcrumb in the top bar: click the workspace
name to return to its datasets, or the dataset name to return to its batches.
The *Samples* pane also has a **Back to batches** button.

## Get data in and analyse it

- **[Import data files](../guides/import-files.md)** — get raw instrument
  files into Mascope and copy the automatically processed acquisition batches
  into your workspace.
- **[Build a target collection and run matching](../guides/target-collections.md)**
  — tell Mascope what compounds to look for, and score every sample against
  them.

Once samples and matches exist, the remaining tabs come alive:

- **Batch** — compare matches across all samples in the batch; the
  *Targets* / *Assignments* switch above the browser decides whether it plots
  target matches or the batch peaks you select.
- **Sample** — the selected sample's sum spectrum and detected peaks, with
  interactive peak assignment: the ledger of what each peak was assigned, the
  peak inspector, and the assignment time series.
- **Match** — one ion's match in detail: per-isotope spectra, the timeseries of
  matched peaks, and the match parameters.

The Sample and Match tabs answer different questions and both stay available:
Match reads one *target* ion across the batch, Sample reads every *peak* of one
sample. [Peak assignment](../how-it-works/peak-assignment.md) is what fills the
Sample tab's ledger; switching it off leaves the targeted workflow, and the
Match tab, exactly as they are.

The [Concepts](../concepts/index.md) page explains the terms these views use;
[How it works](../how-it-works/index.md) covers the processing behind them.
