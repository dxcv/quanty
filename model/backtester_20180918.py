import pandas as pd
import numpy as np
import itertools
import time
from pandas.tseries.offsets import Day
from IPython.core.debugger import set_trace
from tqdm import tqdm, tqdm_notebook

# Custom modules
from .dual_momentum import DualMomentumPort
from .backtester_base import BacktesterBase



class Backtester1(BacktesterBase):
    
    def __init__(self, params, **opt):

        # 매일 기록
        self.eq_value = []
        self.wealth = []
        
        # 의사결정일에만 기록
        self.weight = []
        
        # 리밸일에만 기록
        self.hold = []

        BacktesterBase.__init__(self, params, Port=DualMomentumPort, **opt)
        
    
    def _get_last_from(self, li, inner_idx=None, alt=None):
        try:
            if inner_idx is not None:
                out = li[-1].copy()[inner_idx]
            else:
                out = li[-1].copy()

        except:
            out = alt
            
        return out
        
    
    def _begin_of_day(self):
        trade_amount_ = trade_cashflow_ = cost_ = 0
        
        hold_ = self._get_last_from(self.hold, alt=pd.Series())
        weight_ = self._get_last_from(self.weight, alt=pd.Series())
        eq_value_ = self._get_last_from(self.eq_value, alt=pd.Series())
        cash_ = self._get_last_from(self.wealth, inner_idx=-2, alt=self.cash)
        
        return hold_, cash_, weight_, trade_amount_, trade_cashflow_, cost_, eq_value_

    
    def _end_of_day(self, date, hold_, cash_, eq_value_):
        
        try:
            #eq_value_ = hold_ * self.p_close.loc[:date].iloc[-1] # 이게 더 빠름
            eq_value_ = hold_ * self.p_close.loc[date]
            
        except:
            pass
            
        value_ = eq_value_.sum()
        nav_ = value_ + cash_
        return eq_value_, value_, nav_    
    
    
    
    def _run(self):
        trade_due = -1
            
        for date in tqdm_notebook(self.dates):
            if date in self.p_close.index: 
                trade_due -= 1
                
            # Begin of the day
            hold_, cash_, weight_, trade_amount_, trade_cashflow_, cost_, eq_value_ = self._begin_of_day()
                        
            # 0. 리밸런싱 실행하는 날
            if trade_due==0:
                trade_amount_, trade_cashflow_, cost_, cash_, hold_ = self._rebalance(date, hold_, cash_, weight_)
                self.hold.append(hold_)

            # 1. 리밸런싱 비중결정하는 날
            elif date in self.dates_asof:
                weight_, trade_due = self._positionize(date, weight_, trade_due)                
                self.weight.append(weight_)
              
            # 2. 아무일도 없는 날
            #else:
            #    pass
                
            # End of the day
            eq_value_, value_, nav_ = self._end_of_day(date, hold_, cash_, eq_value_)
            
            #self.hold.append(hold_)
            self.eq_value.append(eq_value_)
            self.wealth.append([trade_amount_, value_, trade_cashflow_, cost_, cash_, nav_])


        # 종목별 시그널, 포지션
        self.weight = pd.DataFrame(self.weight, index=self.dates_asof)
        
        # Daily Booking
        #self.hold = pd.DataFrame(self.hold, index=self.dates)
        #self.eq_value = pd.DataFrame(self.eq_value, index=self.dates)
        self.wealth = pd.DataFrame(self.wealth, index=self.dates, columns=['trade_amount', 'value', 'trade_cashflow', 'cost', 'cash', 'nav'])
        
        # 지수가격(normalized)
        cum = self.p_close.reindex(self.dates, method='ffill')
        cum['DualMomentum'] = self.wealth['nav']
        self.cum = cum / cum.bfill().iloc[0]



    def _rebalance(self, date, hold_, cash_, pos_tobe_):#, eq_value_):
        if self.trade_prev_nav_based:
            pos_prev_amount = hold_*self.p_close.loc[:date-Day()].iloc[-1]
            #pos_prev_amount = eq_value_ #hold_*self.p_close.loc[:date-Day()].iloc[-1]
        else:
            pos_prev_amount = hold_*self.p_close.loc[date]
            
        # Planning
        nav_prev = pos_prev_amount.sum() + cash_
        pos_amount = self.gr_exposure * nav_prev * pos_tobe_
        pos_buffer = nav_prev - pos_amount.sum()
        amount_chg = pos_amount.sub(pos_prev_amount, fill_value=0)
        amount_buy_plan = amount_chg[amount_chg>0]
        amount_sell_plan = -amount_chg[amount_chg<0]

        # Sell first
        p_sell_ = self.p_sell.loc[date]
        share_sell = amount_sell_plan.div(p_sell_).dropna()
        share_sell.where(share_sell.lt(hold_), hold_, inplace=True)
        share_sell.where(pos_tobe_>0, hold_, inplace=True) # 비중 0는 완전히 팔아라
        amount_sell = share_sell*p_sell_
        amount_sell_sum = amount_sell.sum()
        cost_sell = amount_sell_sum * self.expense
        cash_ += (amount_sell_sum - cost_sell)

        # Buy next
        p_buy_ = self.p_buy.loc[date]
        amount_buy_plan_sum = amount_buy_plan.sum()
        amount_buy = amount_buy_plan * np.min([amount_buy_plan_sum, cash_-pos_buffer]) / amount_buy_plan_sum
        amount_buy_sum = amount_buy.sum()
        share_buy = amount_buy.div(p_buy_).dropna()
        cost_buy = amount_buy_sum * self.expense
        cash_ += (-amount_buy_sum - cost_buy)

        # 매매결과
        cost_ = cost_buy + cost_sell
        trade_cashflow_ = amount_sell_sum - amount_buy_sum
        trade_amount_ = amount_sell_sum + amount_buy_sum

        # 최종포지션
        hold_ = hold_.add(share_buy, fill_value=0).sub(share_sell, fill_value=0).dropna()
        
        return trade_amount_, trade_cashflow_, cost_, cash_, hold_


    def _positionize(self, date, weight_asis_, trade_due):
        weight_ = self.port.weight.loc[date]
        
        if weight_.sub(weight_asis_, fill_value=0).abs().sum()!=0:
            trade_due = self.trade_delay

        return weight_, trade_due
    
    
    
class Backtester(BacktesterBase):
    
    def __init__(self, params, **opt):

        # 매일 기록
        self.book = []
        self.book_items = ['trade_amount', 'value', 'trade_cashflow', 'cost', 'cash', 'nav']
        self.i_trade_amount = self.book_items.index('trade_amount')
        self.i_value = self.book_items.index('value')
        self.i_trade_cashflow = self.book_items.index('trade_cashflow')
        self.i_cost = self.book_items.index('cost')
        self.i_cash = self.book_items.index('cash')
        self.i_nav = self.book_items.index('nav')
        self.book_items_n = len(self.book_items)
        
        # 리밸일에만 기록
        self.hold = []
        self.weight = []
        self.eq_value = []
        
        BacktesterBase.__init__(self, params, Port=DualMomentumPort, **opt)
        
            
    def _last_of(self, which, alt=None):
        try:
            return which[-1]
            
        except:
            return (None, alt)
        
          
    def _hold_last(self):
        return self._last_of(self.hold, alt=pd.Series())
        
                
    def _cash_last(self):
        date, book_ = self._last_of(self.book, alt=[])
        
        # len(book_)=0 인 경우는 없다. 
        # 이 함수가 불러질 때는, 적어도 book이 하나이상 채워졌을 때이다. 
        return date, book_[self.i_cash] 
        
        
    def _weight_last(self):
        return self._last_of(self.weight, alt=pd.Series())
        
        
    def _book(self, trade_amount_, value_, trade_cashflow_, cost_, cash_):
        nav_ = value_ + cash_
        
        book_ = [0]*self.book_items_n
        book_[self.i_trade_amount] = trade_amount_
        book_[self.i_value] = value_
        book_[self.i_trade_cashflow] = trade_cashflow_
        book_[self.i_cost] = cost_
        book_[self.i_cash] = cash_
        book_[self.i_nav] = nav_
        return book_    
    
    
    def _fill_book(self, date):
        
        if len(self.book)!=0:
            date_last = self.book[-1][0]
            dates_update = self.dates[(date_last<self.dates) & (self.dates<=date)]
            
            if len(dates_update)!=0:
                hold_last = self._hold_last()[1]
                cash_last = self._cash_last()[1]
            
                p_close = self.p_close[hold_last.index].reindex(dates_update, method='ffill')
                eq_value_update = p_close.mul(hold_last, axis=1)
                value_update = eq_value_update.sum(axis=1)

                book_update = np.zeros((len(dates_update), self.book_items_n))
                book_update[:,self.i_value] = value_update
                book_update[:,self.i_cash] = cash_last
                book_update[:,self.i_nav] = value_update + cash_last

                self.book += zip(dates_update, book_update.tolist())
                
        else:
            self.book.append((date, self._book(0, 0, 0, 0, self.cash)))
    
    
    
    def _df_of(self, which, columns=None):
        return pd.DataFrame.from_dict(dict(which), orient='index', columns=columns).fillna(0).sort_index()
    

    def _rebalance(self, date, weight_):
        # 이게 있으면, 리밸일마다 기록되는 것들(eq_value, hold 등)이 기록이 안되는 경우가 있다. 
        #if weight_.sub(self._weight_last()[1], fill_value=0).abs().sum()==0:
        #    return
        
        i_date_trade = self.dates.get_loc(date) + self.trade_delay
        
        if i_date_trade > len(self.dates)-1:
            return
        
        else:
            date_trade = self.dates[i_date_trade]
            
        #date_trade = self.dates[self.dates.get_loc(date) + self.trade_delay]

        if (date_trade in self.p_close.index):# & (date_trade <= pd.Timestamp(self.end)):
            self._fill_book(date_trade-Day())
            #set_trace()
            trade_amount_, trade_cashflow_, cost_, cash_, hold_ = self._trade(date_trade, weight_, self._hold_last()[1], self._cash_last()[1])
            eq_value_ = self._eq_value(date_trade, hold_)
            book_ = self._book(trade_amount_, eq_value_.sum(), trade_cashflow_, cost_, cash_)
            
            self.eq_value.append((date_trade, eq_value_))
            self.book.append((date_trade, book_))
            self.hold.append((date_trade, hold_))
            #self.weight.append((date_trade, weight_))
            

    def _positionize(self, date):
        self._fill_book(date)
        weight_ = self.port.portfolize(date, book=self.book)
        self.weight.append((date, weight_))
        return weight_
    
    
    def _cum(self):
        cum = self.p_close.reindex(self.dates, method='ffill')
        cum['DualMomentum'] = self.book['nav']
        return cum / cum.bfill().iloc[0]
    
    
    def _run(self):
        #set_trace()
        for date in tqdm_notebook(self.dates_asof):
            weight_ = self._positionize(date)
            self._rebalance(date, weight_)
        
        self._fill_book(self.end)
        self.book = self._df_of(self.book, columns=self.book_items)
        self.hold = self._df_of(self.hold)
        self.eq_value = self._df_of(self.eq_value)
        self.weight = self._df_of(self.weight)
        self.cum = self._cum()
