- **A batch stage that cannot read one sample no longer runs again on every
  press.** A stage that skipped a sample whose file could not be read, such as a
  moved peak file, stayed out of date, so *Detect series*, *Read co-variation*
  and *Read batch checks* each ran the untargeted search again first, with the
  same sample failing each time. Such a stage is now current with the samples
  it skipped, and the batch's assignment status and *Details* name them. A
  skipped sample whose file reads again makes the stage out of date, and the
  next pass reads it (mascope#3356).
