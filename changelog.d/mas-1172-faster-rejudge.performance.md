- **Assign batch judges a sample again in about two seconds.** A pass judges each
  sample again after every stage that writes in it. A later judging now reads
  the file's resolving power and the sample's lists once for the whole pass,
  predicts each ion's isotope envelope once, and elects one reading per peak
  without going through the peaks row by row. It took about 10 s and takes 1.3
  to 2.5 s on the demo's uronium batch, and one pass over the batch's 81
  samples took 31 minutes instead of 52 or more. A sample's run elects its known set faster
  too. The tiers are the same as before.
