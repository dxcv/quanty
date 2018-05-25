import numpy as np
import pandas as pd
from IPython.core.debugger import set_trace
from pandas.tseries.offsets import Day
from numba import njit, float64, int64, int32, boolean


@njit(float64[:](int64, int64[:], float64[:,:], int32[:,:]))
def _signal_nb(i_date, i_ref, p_ref_val, sig_w):
    r = p_ref_val[i_date] / p_ref_val[i_ref[i_ref<i_date][-12:]] - 1.0
    r *= sig_w
    
    n = p_ref_val.shape[1]
    out = np.empty(n)
    
    for col in range(n):
        out[col] = r[:,col].sum()
    
    return out
    
    
@njit(boolean(int64, int64, int64, float64[:,:], int64))
def _has_ma_mtum_single_nb(i_date, term_short, term_long, p_ref_val, i_asset):
    p = p_ref_val[:i_date+1,i_asset]
    p_ma_short = p[-term_short:].mean()
    p_ma_long = p[-term_long:].mean()
    return p_ma_short>p_ma_long
    

@njit(boolean[:](int64, int64, int64, float64[:,:]))
def _has_ma_mtum_nb(i_date, term_short, term_long, p_ref_val):
    n = p_ref_val.shape[1]
    out = np.empty(n, dtype=boolean)
    
    for i_asset in range(n):
        out[i_asset] = _has_ma_mtum_single_nb(i_date, term_short, term_long, p_ref_val, i_asset)
        
    return out
    
    
class DualMomentum(object):
    
    def __init__(self, **params):
        #mode
        #n_picks
        #sig_w
        #p_ref
        #p_close
        #assets_member_bet
        #cash_equiv
        #riskfree
        #market
        #rf_trend
        #support_cash
        #overall_market_check
        self.__dict__.update(**params)
        
        dates_ref = pd.date_range(self.p_ref.index[0], self.p_ref.index[-1], freq='M')
        self.i_ref = self.p_ref.index.get_indexer(dates_ref, method='ffill')
        self.p_ref_val = self.p_ref.values
        self.p_close_val = self.p_close.values
        self.sig_w = self.sig_w.reshape(-1,1)


    def get(self, date):
        i_date = self.p_ref.index.get_loc(date, method='ffill')
        sig_ = self._signal(i_date)
        selection_, ranks_ = self._selection(sig_, i_date, date)
        return selection_, ranks_, sig_
    
    
    def _signal(self, i_date):
        sig = _signal_nb(i_date, self.i_ref, self.p_ref_val, self.sig_w)
        return pd.Series(sig, index=self.assets_member.bet)
    
    
    def _signal2(self, date):
        n_sig_w = len(self.sig_w)
        n_back = n_sig_w*31 + 40
        date_from = date - n_back*Day()
        date_to = date - 0*Day()
        #set_trace()
        p = self.p_ref.loc[date_from:date_to].resample('M').ffill().iloc[-n_sig_w-1:]
        #p = self.p_ref.loc[date_from:date_to].resample('M').asfreq().iloc[-n_sig_w-1:]
        r = (p.iloc[-1]/p.iloc[:-1]-1).replace(np.inf, np.nan)
        sig_w = self.sig_w[-len(r):]
        
        sig = r.mul(sig_w, axis=0).sum(skipna=False)        
        sig.index = self.assets_member.bet
        
        not_tradable = ~self._is_tradable(date)
        sig.loc[not_tradable] = np.nan
        return sig
    
    
    def _is_tradable(self, i_date):
        #return self.p_close.loc[:date].iloc[-1].notnull()
        return self.p_close.iloc[i_date].notnull()
            
    
    def _selection(self, sig, i_date, date):
        is_tradable = self._is_tradable(i_date)
        sig.loc[~is_tradable] = np.nan
        
        #is_rf_tradable = self._is_tradable(date, self.riskfree)
        has_rf_ma_mtum = self._has_ma_mtum_single(date, self.rf_trend, self.riskfree)
        has_rf_positive_sig = sig.loc[self.riskfree]>=0
        
        if self.support_cash and is_tradable[self.riskfree] and (has_rf_ma_mtum or has_rf_positive_sig):
            pos, ranks = self._get_default_selection(date, sig, self.n_picks-1, i_date)
            pos_rf = self.n_picks - pos.sum()
            pos_cash = 0
            
            if has_rf_ma_mtum and has_rf_positive_sig:
                pass
            
            elif has_rf_ma_mtum:
                pos_rf = int(pos_rf*0.5)
              
            elif has_rf_positive_sig:
                pos_rf = int(pos_rf*0.5)
          
        else:
            pos, ranks = self._get_default_selection(date, sig, self.n_picks, i_date)
            pos_rf = 0

            if is_tradable[self.cash_equiv]:
                pos_cash = self.n_picks - pos.sum()
                
            else:
                pos_cash = 0
                
          
        pos.loc[self.riskfree] += pos_rf  
        
        try:
            pos.loc[self.cash_equiv] += pos_cash
            
        except:
            pos.loc[self.cash_equiv] = pos_cash
        
        return pos, ranks
    
    
    def _get_default_selection(self, date, sig, n_picks, i_date):
        has_ma_mtum = self._has_ma_mtum(i_date, self.self_trend)
        
        score = sig.copy()
        score.loc[~has_ma_mtum] = np.nan
        ranks = score.rank(ascending=False, na_option='bottom')
        
        if self.mode=='DualMomentum':
            pos = (score>0) & (ranks<1+n_picks)
            if self.overall_market_check:
                pos &= (sig.loc[self.market]>0)
          
        elif self.mode=='RelativeMomentum':
            pos = ranks<1+n_picks
          
        elif self.mode=='AbsoluteMomentum':
            pos = (score>0)
            if self.overall_market_check:
                pos &= (sig.loc[self.market]>0)
                
        return pos.astype(int), ranks
    
    
    
    def _has_ma_mtum(self, i_date, terms):
        if terms is not None:
            #p = self.p_ref.loc[:date]
            #p_ma_short = p.iloc[-terms[0]:].mean()
            #p_ma_long = p.iloc[-terms[1]:].mean()
            #has_ma_mtum = p_ma_short > p_ma_long
            #set_trace()
            has_ma_mtum = _has_ma_mtum_nb(i_date, terms[0], terms[1], self.p_ref_val)
            #has_ma_mtum = pd.Series(has_ma_mtum, index=self.assets_member.bet)
            #has_ma_mtum.index = self.assets_member.bet
        
        else:
            has_ma_mtum = pd.Series(index=self.assets_member.bet)
            has_ma_mtum[:] = True
            
        return has_ma_mtum

      
    def _has_ma_mtum_single(self, i_date, terms, asset_ref):
        if terms is not None:
            #p = self.p_ref.loc[:date, asset_ref]
            #p_ma_short = p.iloc[-terms[0]:].mean()
            #p_ma_long = p.iloc[-terms[1]:].mean()
            #has_ma_mtum = p_ma_short > p_ma_long
            i_asset = self.p_ref.columns.get_loc(asset_ref)
            has_ma_mtum = _has_ma_mtum_single_nb(i_date, terms[0], terms[1], self.p_ref_val, i_asset)
        
        else:
            has_ma_mtum = True
            
        return has_ma_mtum
    