- **A side lobe's peak no longer gets a resolution of millions.** Read
  without the vendor library, an Orbitrap file's summed spectrum took each
  peak's resolution from its centroids in the scans, and the instrument can
  write a side lobe's centroid at hundreds of times its real resolution. One
  such centroid made the peak far too narrow: its area shrank by as much,
  and it was never recognised as the side lobe it is. A centroid now counts
  at most twice what its scan resolves at that m/z, and on the demo files no
  peak reads more than three times the vendor library's resolution.
