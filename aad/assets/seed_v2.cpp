#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <iostream>
#include <numeric>
#include <vector>
using namespace std;
struct Rect { int a,b,c,d; };
struct Point { int x,y,r; };
uint64_t random_state=137;
inline uint32_t rnd() { random_state^=random_state<<7; random_state^=random_state>>9; return uint32_t(random_state); }
inline double uniform01() {return (rnd()+0.5)/4294967296.0;}
inline int area(Rect r) {return (r.c-r.a)*(r.d-r.b);}
inline bool overlap(Rect x,Rect y) {return x.a<y.c && y.a<x.c && x.b<y.d && y.b<x.d;}
inline bool legal(Rect r,Point p) {return 0<=r.a && r.a<=p.x && p.x<r.c && r.c<=10000 && 0<=r.b && r.b<=p.y && p.y<r.d && r.d<=10000;}
inline double quality(Rect rect,Point p) {double v=double(min(area(rect),p.r))/max(area(rect),p.r); return 1-(1-v)*(1-v);}
// AAD-BEGIN parameters
constexpr double START_T=0.035;
constexpr double END_T=0.000005;
constexpr double TIME_SECONDS=4.25;
constexpr double EXPAND_RATE=0.48;
constexpr double TRANSLATE_RATE=0.20;
constexpr double RESHAPE_RATE=0.27;
constexpr double STEP_SCALE=0.65;
// AAD-END parameters

// AAD-BEGIN proposal
Rect propose(int i,const vector<Rect>& rs,const vector<Point>& ps,double progress) {
    Rect q=rs[i]; auto p=ps[i]; int w=q.c-q.a,h=q.d-q.b;
    double u=uniform01(); int side=rnd()%4;
    int step=1+int(sqrt(p.r)*STEP_SCALE*pow(uniform01(),2.0));
    if(u<EXPAND_RATE) {
        int span=(side%2==0?h:w);
        int need=max(1,(p.r-area(q))/span);
        step=min(step,need);
        if(side==0)q.a=max(0,q.a-step);
        if(side==1)q.b=max(0,q.b-step);
        if(side==2)q.c=min(10000,q.c+step);
        if(side==3)q.d=min(10000,q.d+step);
    } else if(u<EXPAND_RATE+TRANSLATE_RATE) {
        int delta=(rnd()%2?1:-1)*step;
        if(side%2==0) {delta=max(-q.a,min(10000-q.c,delta));delta=max(p.x-q.c+1,min(p.x-q.a,delta));q.a+=delta;q.c+=delta;}
        else {delta=max(-q.b,min(10000-q.d,delta));delta=max(p.y-q.d+1,min(p.y-q.b,delta));q.b+=delta;q.d+=delta;}
    } else if(u<EXPAND_RATE+TRANSLATE_RATE+RESHAPE_RATE) {
        int target=(rnd()%3==0?p.r:area(q));
        int nw=max(1,min(10000,int(w*exp((uniform01()-.5)*1.4))));
        int nh=max(1,min(10000,target/nw));
        int minx=max(0,p.x-nw+1),maxx=min(p.x,10000-nw);
        int miny=max(0,p.y-nh+1),maxy=min(p.y,10000-nh);
        q.a=minx+rnd()%(maxx-minx+1);q.c=q.a+nw;
        q.b=miny+rnd()%(maxy-miny+1);q.d=q.b+nh;
    } else {
        if(side==0)q.a=min(p.x,q.a+step);
        if(side==1)q.b=min(p.y,q.b+step);
        if(side==2)q.c=max(p.x+1,q.c-step);
        if(side==3)q.d=max(p.y+1,q.d-step);
    }
    return q;
}
// AAD-END proposal

// AAD-BEGIN conflict
bool trim_neighbor(Rect old,Point p,Rect inserted,Rect& chosen) {
    int best=-1;
    auto consider=[&](Rect q){if(legal(q,p)&&!overlap(q,inserted)&&area(q)>best){best=area(q);chosen=q;}};
    if(p.x<inserted.a){Rect q=old;q.c=min(q.c,inserted.a);consider(q);}
    if(p.x>=inserted.c){Rect q=old;q.a=max(q.a,inserted.c);consider(q);}
    if(p.y<inserted.b){Rect q=old;q.d=min(q.d,inserted.b);consider(q);}
    if(p.y>=inserted.d){Rect q=old;q.b=max(q.b,inserted.d);consider(q);}
    return best>=0;
}
// AAD-END conflict

// AAD-BEGIN schedule
double temperature(double progress) {return START_T*pow(END_T/START_T,progress);}
// AAD-END schedule

// AAD-BEGIN search
vector<Rect> solve(const vector<Point>& ps,double seconds) {
    const int n=ps.size();vector<Rect> rs(n),best;vector<double> val(n);
    for(int i=0;i<n;i++){rs[i]={ps[i].x,ps[i].y,ps[i].x+1,ps[i].y+1};val[i]=quality(rs[i],ps[i]);}
    double score=accumulate(val.begin(),val.end(),0.0),best_score=score;
    best=rs;auto start=chrono::steady_clock::now();double progress=0,temp=START_T;
    array<int,201> changed;array<Rect,201> next;array<double,201> nextval;
    for(uint64_t iter=0;;iter++) {
        if((iter&255)==0){progress=chrono::duration<double>(chrono::steady_clock::now()-start).count()/seconds;if(progress>=1)break;temp=temperature(progress);}
        int i=rnd()%n;Rect q=propose(i,rs,ps,progress);
        if(!legal(q,ps[i]))continue;
        int count=1;changed[0]=i;next[0]=q;nextval[0]=quality(q,ps[i]);double gain=nextval[0]-val[i];bool ok=true;
        for(int j=0;j<n;j++)if(i!=j&&overlap(q,rs[j])){
            Rect trimmed;if(!trim_neighbor(rs[j],ps[j],q,trimmed)){ok=false;break;}
            changed[count]=j;next[count]=trimmed;nextval[count]=quality(trimmed,ps[j]);gain+=nextval[count]-val[j];count++;
        }
        if(ok && (gain>=0 || uniform01()<exp(gain/temp))){
            for(int k=0;k<count;k++){rs[changed[k]]=next[k];val[changed[k]]=nextval[k];}
            score+=gain;
            if(score>best_score){best_score=score;best=rs;}
        }
    }
    return best;
}
// AAD-END search

int main(int argc,char**argv){
    ios::sync_with_stdio(false);cin.tie(nullptr);
    int n;if(!(cin>>n))return 1;vector<Point> ps(n);for(auto&p:ps)cin>>p.x>>p.y>>p.r;
    double seconds=TIME_SECONDS;if(argc>1)seconds=stod(argv[1]);
    auto result=solve(ps,seconds);for(auto r:result)cout<<r.a<<' '<<r.b<<' '<<r.c<<' '<<r.d<<'\n';
}
