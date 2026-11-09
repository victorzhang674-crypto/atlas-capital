import time, numpy as np
from statsmodels.tsa.stattools import coint
rng=np.random.default_rng(0)
import sys; n=int(sys.argv[1])
def hurst(s,maxlag=50):
    lags=np.arange(2,maxlag+1)
    sd=[np.std(s[l:]-s[:-l]) for l in lags]
    return np.polyfit(np.log(lags),np.log(sd),1)[0]
def ou(hl,n,sig=0.01):
    phi=2**(-1/hl); s=np.zeros(n)
    for t in range(1,n): s[t]=phi*s[t-1]+rng.normal(0,sig)
    return s
t=time.time()
for _ in range(50):
    x=np.cumsum(rng.normal(0,.01,n)); y=x+ou(10,n)
    coint(y,x,trend="c",autolag="aic")
print("coint ms",(time.time()-t)/50*1000)
for hl in (5,10,15,20,30,45,60):
    rec=0;H=[]
    for k in range(100):
        f=np.cumsum(rng.normal(0,.01,n)); x=f+np.cumsum(rng.normal(0,.005,n)); y=0.1+0.9*x+ou(hl,n,0.008)
        p=max(coint(y,x)[1],coint(x,y)[1]); rec+=p<0.05
        b=np.polyfit(x,y,1); H.append(hurst(y-np.polyval(b,x)))
    print(hl,"power",rec/100,"H",round(np.mean(H),3))
fp=0;H=[]
for k in range(300):
    f=np.cumsum(rng.normal(0,.01,n)); x=f+np.cumsum(rng.normal(0,.004,n)); y=f+np.cumsum(rng.normal(0,.004,n))
    p=max(coint(y,x)[1],coint(x,y)[1]); fp+=p<0.05
    b=np.polyfit(x,y,1); H.append(hurst(y-np.polyval(b,x)))
print("rw fp (EG only)",fp/300,"H",round(np.mean(H),3), "corr", np.corrcoef(np.diff(x),np.diff(y))[0,1])
