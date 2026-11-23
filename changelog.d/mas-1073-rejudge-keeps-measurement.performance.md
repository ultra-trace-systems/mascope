- **A pass of the batch stages keeps what it measured of each sample.** After
  each stage that writes in a sample, the pass judges the sample again. A
  later judging now fits only the readings a stage wrote or changed, and
  takes the rest from the earlier judging, with the same tiers. That saves
  about a quarter of each later judging. While a stage judges its samples
  again, the pass's progress bar moves sample by sample, and the batch's
  status read reports the same progress. The stage's result line counts the
  readings fitted and kept, and names the samples whose run recorded no
  measurement model, which are fitted whole every time. (MAS-1073, #3364)
