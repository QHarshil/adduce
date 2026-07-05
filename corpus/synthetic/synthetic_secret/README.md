# synthetic_secret

Positive control: the config commits an AWS access key (Amazon's documented
example key, not a live credential). R-PORT-004 must fail, and the key value
must never appear in any report output.
