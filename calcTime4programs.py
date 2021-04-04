#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Mar 27 17:28:01 2021

@author: arest
"""
import sys, os, re, copy, shutil,io
import argparse
from pdastro import pdastroclass,AnotB,AandB
import pandas as pd
import numpy as np
from astropy import time
from astroplan import Observer
from astropy import units as u
ctio = Observer.at_site("CTIO")

programlist = [
'2019A-0065_Shen1',
'2019B-0304_Martini',
'2020A-0906_eFEDS',
'2021A-0037_Shen2',
'2021A-0275_YSE',
'2020B-0053_DEBASS',
'2021A-0113_DDF',
'2021A-0148_DESI',
'2020A-0415_EtaCar'
]

program2fieldpattern = {
'2019A-0065_Shen1':      ['^SN\-C3','^S\-CVZ','SN\-X\d'],
'2019B-0304_Martini':   ['E1','E3'],
'2020A-0906_eFEDS':     ['^eFEDS'],
'2021A-0037_Shen2':      ['^CO\d$'],
'2021A-0275_YSE':       ['^\d\d\d\.\w+\.[abcde]'],
'2020B-0053_DEBASS':    ['^2021\w+'],
'2021A-0113_DDF':       ['^COSMOS','^DECaPS.*'],
'2021A-0148_DESI':      ['^TILEID\:\s+\d+'], 
'2020A-0415_EtaCar':    ['^ec\d\d\d\d'],
'2019A-0305_Drlica_TRADE':['^DELVE'],
'2021A-0244_Miller_TRADE':['^n2997'],
'2021A-0010_Rector_TRADE':['^Cha'],
'2021A-0149_Zenteno_TRADE':['^BLA'],
'STANDARDS':             ['^E','^SDSS','^LTT','C26202'],
'TECHSETUP':               ['^pointing','^MaxVis']
    }


class calcTimeclass(pdastroclass):
    def __init__(self):
        pdastroclass.__init__(self)
        
        self.qcinv = pdastroclass()
        self.minimal_overhead = 28 # in seconds
        
        self.verbose=0
        self.debug=0
        
        self.warnings = []
        
        self.t = pd.DataFrame(columns=['blockID','assigned_program','program','UTfirst','UTlast','dt_block_h','dt_prevgap_sec','dt_nextgap_sec','dt_gaps_sec','dt_block_full_h','twi','dt_charged_h'])
        self.summary = pdastroclass()
        self.summary.t = pd.DataFrame(columns=['assigned_program','t_total'])
        
        self.programcol_formatter='{:<24}'.format
        
        self.horizons = [18,15,12]
        self.twi_charge_fraction = (1.0,2/3,1/3,0.0)

    def addwarning(self,warningstring):
        print(warningstring)
        self.warnings.append(warningstring)
        return(0)


    def add_arguments(self, parser=None, usage=None, conflict_handler='resolve'):
        if parser is None:
            parser = argparse.ArgumentParser(usage=usage, conflict_handler=conflict_handler)
            
        parser.add_argument('qcinvfile')

        parser.add_argument('-s','--save', nargs='*', help="Save the tables. if no argument is specified, then the input name is used as basename")
        parser.add_argument('-r','--reassign', nargs=2, action="append", help="reassign one program block to another program")
            
        parser.add_argument('-v', '--verbose', action='count', default=0)
        parser.add_argument('-d', '--debug', action='count', default=0)

        return(parser)
    
    def create_fieldpattern2program(self):
        self.fieldpattern2program ={}
        self.fieldpatterns=[]
        for program in program2fieldpattern:
            for fieldpattern in program2fieldpattern[program]:
                self.fieldpatterns.append(fieldpattern)
                self.fieldpattern2program[fieldpattern]={}
                self.fieldpattern2program[fieldpattern]['program']=program
                self.fieldpattern2program[fieldpattern]['compiled']=re.compile(fieldpattern)

        return(0)            
    
    def readqcinv(self,filename):
        # load the qcinv file and fix it: remove \n and remove extra headers
        if self.verbose: print('loading ',filename)
        self.filename = filename
        lines = open(filename,'r').readlines()
        for i in range(len(lines)-1,-1,-1):
            lines[i] = re.sub('\\n$','',lines[i])
            if re.search('^\s*\#',lines[i]):
                if i==0:
                    lines[i] = re.sub('^\#',' ',lines[i])
                else:
                    del(lines[i])

        #insert dummy line: otherwise the last column width is set to the width of the last column in the first row
        s = re.sub('\w+$','dummydummydummydummydummy',lines[1])
        lines.insert(1,s)
        
        # 'time' is too long for the column width and butts into the secz column,
        # which confuses pandas reading, and it combines the time and secz column.
        # hack: rename time to tim
        lines[0]=re.sub('time','xx  ',lines[0])
        
        # remove any empty lines at the end of the lines
        while lines[-1]=='':
            lines.pop(-1)
            
        # Remove the extra "MJD = ..." line at the end
        if re.search('^MJD',lines[-1]): 
            m = re.search('^MJD\s*=\s*(\w+)',lines[-1])
            if m is None:
                raise RuntimeError('Could not get the MJD from the qcinv file! the last line is %s, but should be something like "MJD = 59146 (Oct 23/Oct 24)"' % (lines[-1]))
            MJD = int(m.groups()[0])
            print('MJD: %d' % MJD)
            
            # define tonight for CTIO.
            # Subtract 0.4 from MJD: if the MJD is not *before* the night starts, it tonight
            # returns as start value the given MJD. Subtracting 0.4 makes sure MJD is before the night starts.
            self.tonight = ctio.tonight(time.Time(MJD-0.4, scale='utc',format='mjd'))
            print('Night Start:',self.tonight[0].to_value('isot'))
            print('Night End  :',self.tonight[1].to_value('isot'))

            self.twi={}
            for horizon in self.horizons:
                self.twi[horizon] =  ctio.tonight(time.Time(MJD-0.4, scale='utc',format='mjd'),horizon=-horizon*u.deg)
                if self.verbose: print('%d deg twilight: %s %s' % (-horizon,self.twi[horizon][0].to_value('isot'),self.twi[horizon][1].to_value('isot')))
            # Remove line with MJD
            lines.pop(-1)
        else:
            raise RuntimeError('Could not get the MJD from the qcinv file! the last line should be something like "MJD = 59146 (Oct 23/Oct 24)"')
        
        # parse the qcinv file
        self.qcinv.t = pd.read_fwf(io.StringIO('\n'.join(lines)))
        #self.qcinv.write(indices=range(1,10))
        
        # rename tim to time again...
        self.qcinv.t.rename(columns={'xx': 'time'},inplace=True)
       
        #remove dummy line
        self.qcinv.t = self.qcinv.t[1:]
        #self.qcinv.t.drop(index=[0],inplace=True)

        if self.verbose:
            print('file loaded, first 10 lines:')
            self.qcinv.write(indices=range(1,11))
        return(0)
    
    def fill_qcinv_table(self):
        #m = re.compile('^2')
        
        # set format of dt_h*.
        self.qcinv.t['utdate']=None
        self.qcinv.t['ut_decimal']=np.nan
        self.qcinv.t['twi']=0
        self.qcinv.default_formatters['ut_decimal']='{:.4f}'.format
        
        ix_all = self.qcinv.getindices()
                
        # I just choose a random date. The date itself is not important, it's 
        # just that all UT times 2?:?? have the date before this random date,
        # so that the time difference is correct
        t0 = time.Time('2020-01-02T00:00:00',scale='utc',format='isot')
        
        # get just the dates (not hours) for the beginning and end of the night
        datestart = self.tonight[0].to_value('isot')[:10]
        dateend = self.tonight[1].to_value('isot')[:10]
        
        # reference t 
        t0 = time.Time(dateend+'T00:00:00.00',scale='utc',format='isot')
        
        
        for ix in ix_all:
            # first try if the datestart is the correct date to use.
            tobs = time.Time(datestart+'T'+self.qcinv.t.loc[ix,'ut']+':00', scale='utc')
            dt = tobs - self.tonight[0]
            
            # if it is not after the start of the night, try dateend
            if dt.to_value('hr')<0.0:
                tobs = time.Time(dateend+'T'+self.qcinv.t.loc[ix,'ut']+':00', scale='utc')
                dt = tobs - self.tonight[0]
                # comething is wrong!!
                if dt.to_value('hr')<0.0:
                    raise RuntimeError('dt = %f, could not figure out the UT date for %s that is past the startdate %s' % (dt.to_value('hr'),self.qcinv.t.loc[ix,'ut'],self.tonight[0].to_value('isot')))
            
            # Make sure tobs is before the end of the night
            dt = self.tonight[1] - tobs
            if dt.to_value('hr')<0.0:
                raise RuntimeError('dt = %f, could not figure out the UT date for %s that is before the enddate %s' % (dt.to_value('hr'),self.qcinv.t.loc[ix,'ut'],self.tonight[1].to_value('isot')))
            
            self.qcinv.t.loc[ix,'utdate']=tobs.to_value('isot')
            
            # now get the relative time in decimal hours with respect to t0
            dt = (tobs-t0)
            self.qcinv.t.loc[ix,'ut_decimal'] = dt.to_value('hr')
            
            # check for twilight!
            twi_zone = 0
            for i in range(len(self.horizons)):
                horizon = self.horizons[i]
                if (tobs-self.twi[horizon][0]).to_value('hr')<0.0:
                    twi_zone = i+1
                #print('vvv',(tobs-self.twi[horizon][1]).to_value('hr'),self.qcinv.t.loc[ix,'time']/3600.0)
                if (tobs-self.twi[horizon][1]).to_value('hr')+self.qcinv.t.loc[ix,'time']/3600.0>0.0:
                    twi_zone = i+1
            self.qcinv.t.loc[ix,'twi'] = twi_zone
                    
                
            #print(dt.to_value('hr'))
        if self.verbose>2: self.qcinv.write()
        return(0)

    
    def assignPrograms(self):
        self.create_fieldpattern2program()
        self.qcinv.t['program']=None
        self.qcinv.t['blockID']=0
        
        ixs = self.qcinv.getindices()
        
        blockID = 1
        
        special_programs = {}
        for p in ['UNKNOWN','TECHSETUP','STANDARDS']:
            special_programs[p]={}
            special_programs[p]['counter']=0
            special_programs[p]['pattern']=re.compile('^%s' % p)
        
        for i in range(len(ixs)):
            ix = ixs[i]

            foundflag=False
            for fieldpattern in self.fieldpattern2program:
                m = self.fieldpattern2program[fieldpattern]['compiled']
                if m.search(self.qcinv.t.loc[ix,'Object']):
                    program = self.fieldpattern2program[fieldpattern]['program']
                    if self.verbose>2: print('FOUND! pattern %s matches %s, program %s' % (fieldpattern,self.qcinv.t.loc[ix,'Object'],program))
                    if program=='2020B-0053_DEBASS':
                        if self.qcinv.t.loc[ix,'time']>25:
                            program='2021A-0275_YSE'

                    if program in special_programs:
                        m = special_programs[program]['pattern']
                        if i==0 or (not m.search(self.qcinv.t.loc[ixs[i-1],'program'])):
                            special_programs[program]['counter']+=1
                        program += '%d' %  special_programs[program]['counter']
 
                    self.qcinv.t.loc[ix,'program']=program
                    foundflag=True
                    break
            if not foundflag:
                program='UNKNOWN' 
                m = special_programs[program]['pattern']
                if i==0 or (not m.search(self.qcinv.t.loc[ixs[i-1],'program'])):
                    special_programs[program]['counter']+=1
                program += '%d' %  special_programs[program]['counter']
                self.addwarning('WARNING: Could not find the program for %s in line %d (block %s)' % (self.qcinv.t.loc[ix,'Object'],ix,program))
                self.qcinv.t.loc[ix,'program']=program
            
            # New block ID?
            if i>0: 
                if self.qcinv.t.loc[ixs[i-1],'twi']!=self.qcinv.t.loc[ix,'twi'] or self.qcinv.t.loc[ixs[i-1],'program']!=self.qcinv.t.loc[ix,'program']:
                # if not the first entry AND if different than previous row's program: inc blockID
                #if ix!=ixs[0] and self.qcinv.t.loc[ixs[i-1],'program']!=self.qcinv.t.loc[ix,'program']:
                    blockID+=1
            self.qcinv.t.loc[ix,'blockID']=blockID            
        
    def calcTimes(self):
        self.t['dt_block_h']=self.t['dt_prevgap_sec']=self.t['dt_nextgap_sec']=self.t['dt_gaps_sec']=self.t['dt_block_full_h']=self.t['dt_charged_h']=np.nan
        self.default_formatters['dt_block_h']='{:.4f}'.format
        self.default_formatters['dt_prevgap_sec']='{:.0f}'.format
        self.default_formatters['dt_nextgap_sec']='{:.0f}'.format
        self.default_formatters['dt_gaps_sec']='{:.0f}'.format
        self.default_formatters['dt_block_full_h']='{:.4f}'.format
        self.default_formatters['assigned_program']=self.programcol_formatter

        re_techsetup_standards = re.compile('^TECHSETUP|^STANDARDS')
        blockIDs = self.qcinv.t['blockID'].unique()
        # get info for each block
        for i in range(len(blockIDs)):
            ixs = self.qcinv.ix_inrange('blockID', blockIDs[i],blockIDs[i])
            if len(ixs)==0:
                self.newrow({'blockID':blockIDs[i]})
                self.addwarning('WARNING: could not find any entries for blockID %d' % blockIDs[i])
                continue
            
            twi_zone = self.qcinv.t.loc[ixs,'twi'].unique()
            if len(twi_zone)!=1:
                raise RuntimeError('Could not determine twi_zone')
            
            # first get the difference between last and first UT
            dt_block_h = self.qcinv.t.loc[ixs[-1],'ut_decimal']-self.qcinv.t.loc[ixs[0],'ut_decimal'] 
            
            # exposure time and nominal overhead for last im
            #self.qcinv.write(indices=ixs)
            lastim_dt = (self.qcinv.t.loc[ixs[-1],'time']+self.minimal_overhead)/3600.0
            
            # now add in exposure time of last ecposure and nominal overhead
            dt_block_h += lastim_dt

            # get info for gap
            if i < len(blockIDs)-1:
                ixs_next = self.qcinv.ix_inrange('blockID', blockIDs[i]+1,blockIDs[i]+1)
                dt_nextgap_sec = self.qcinv.t.loc[ixs_next[0],'ut_decimal']-(self.qcinv.t.loc[ixs[-1],'ut_decimal']+lastim_dt)
            else:
                dt_nextgap_sec=0.0
                
            self.newrow({'blockID':blockIDs[i],
                         'assigned_program':self.qcinv.t.loc[ixs[0],'program'],
                         'UTfirst':self.qcinv.t.loc[ixs[0],'ut'],
                         'UTlast':self.qcinv.t.loc[ixs[-1],'ut'],
                         'dt_block_h':dt_block_h,
                         'dt_prevgap_sec':1.0,                    
                         'dt_nextgap_sec':dt_nextgap_sec*3600.0,                   
                         'twi':twi_zone[0]                      
                         })
        
        # assign previous gap times
        # Also, take into account the edge cases of first and last block
        ixs_blocks = self.getindices()
        for i in range(len(ixs_blocks)):
            
            if i==0: 
                self.t.loc[ixs_blocks[i],'dt_prevgap_sec']=0.0
                continue
            
            if i==len(ixs_blocks)-1:
                self.t.loc[ixs_blocks[i],'dt_nextgap_sec']=0.0
            
            self.t.loc[ixs_blocks[i],'dt_prevgap_sec']=self.t.loc[ixs_blocks[i-1],'dt_nextgap_sec']
        
        # If TECHSETUP and STANDARDS is block: eat all the time, so set nextgap from previous block and prevgap from next block to 0.0 !
        for i in range(len(ixs_blocks)-1):
            if re_techsetup_standards.search(self.t.loc[ixs_blocks[i],'assigned_program']):
                if i>0: self.t.loc[ixs_blocks[i-1],'dt_nextgap_sec']=0.0
                if i<len(ixs_blocks)-1: self.t.loc[ixs_blocks[i+1],'dt_prevgap_sec']=0.0
        
        # Add up block time and gap time:
        # TECHSETUP, STANDARDS: eat all gap time
        # all other programs: eat half of each gap time (previous and next)
        for i in range(len(ixs_blocks)):
            if re_techsetup_standards.search(self.t.loc[ixs_blocks[i],'assigned_program']):
                self.t.loc[ixs_blocks[i],'dt_gaps_sec'] = 1.0*(self.t.loc[ixs_blocks[i],'dt_nextgap_sec']+self.t.loc[ixs_blocks[i],'dt_prevgap_sec'])
            else:
                self.t.loc[ixs_blocks[i],'dt_gaps_sec'] = 0.5*(self.t.loc[ixs_blocks[i],'dt_nextgap_sec']+self.t.loc[ixs_blocks[i],'dt_prevgap_sec'])
            
            self.t.loc[ixs_blocks[i],'dt_block_full_h'] = self.t.loc[ixs_blocks[i],'dt_block_h']+self.t.loc[ixs_blocks[i],'dt_gaps_sec']/3600.0
            self.t.loc[ixs_blocks[i],'dt_charged_h'] = self.t.loc[ixs_blocks[i],'dt_block_full_h'] *  self.twi_charge_fraction[self.t.loc[ixs_blocks[i],'twi']]
                
        self.t['program']='-'
                
            
    def reassign_programs(self,reassign):
        if reassign is not None:
            
            for (inprogram,outprogram) in reassign:
                ixs = self.ix_equal('assigned_program',inprogram)
                if len(ixs)==0:
                    self.addwarning('WARNING! could not find any entries for program %s, so that it can be reassigned to %s' % (inprogram,outprogram))
                    continue
                self.t.loc[ixs,'program']=inprogram
                self.t.loc[ixs,'assigned_program']=outprogram
        
    def mkSummary(self):
        print('################\n### SUMMARY:')
        current_programs = self.t['assigned_program'].unique()
        current_mainprograms = AandB(current_programs,programlist)
        extra_programs = AnotB(current_programs,current_mainprograms)
        extra_programs.sort()
        current_mainprograms.sort()
        programs = list(current_mainprograms)
        programs.extend(extra_programs)
        for program in programs:
            ixs = self.ix_equal('assigned_program',program)
            t_total = self.t.loc[ixs,'dt_charged_h'].sum()
            self.summary.newrow({'assigned_program':program,
                                 't_total':t_total})
        self.summary.default_formatters['assigned_program']=self.programcol_formatter
        self.summary.write()
        
    def savetables(self,basename=None):
        if basename is not None:
            if len(basename)==0:
                basename=self.filename
            elif len(basename)==1:
                basename=basename[0]
            else:
                raise RuntimeError('more than one argument for --save is not allowed!')
            print('Saving tables with basename',basename)
            self.qcinv.write(basename+'.times.txt',verbose=2)
            self.write(basename+'.blocks.txt',verbose=2)
            self.summary.write(basename+'.summary.txt',verbose=2)
            
            
        return(0)

if __name__ == "__main__":
    calcTime = calcTimeclass()
    usagestring='USAGE: calcTime.py qcinv_filename'
    parser=calcTime.add_arguments(usage=usagestring)
    args = parser.parse_args()
    
    calcTime.verbose=args.verbose
    calcTime.debug=args.debug

    calcTime.readqcinv(args.qcinvfile)  
    calcTime.fill_qcinv_table()
    calcTime.assignPrograms()
    calcTime.calcTimes()
    calcTime.reassign_programs(args.reassign)
    if args.verbose>1:
        calcTime.qcinv.write()
        calcTime.write()

    calcTime.mkSummary()
    
    calcTime.savetables(basename=args.save)
    
    if len(calcTime.warnings)>0:
        print('THERE WERE WARNINGS!!!')
        for s in calcTime.warnings: print(s)