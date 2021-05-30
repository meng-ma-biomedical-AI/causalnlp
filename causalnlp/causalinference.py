# AUTOGENERATED! DO NOT EDIT! File to edit: nbs/00_causalinference.ipynb (unless otherwise specified).

__all__ = ['CausalModel', 'metalearner_cls_dict', 'metalearner_reg_dict']

# Cell
import pandas as pd
pd.set_option('display.max_columns', 500)
import time
from causalml.inference.meta import BaseTClassifier, BaseXClassifier, BaseRClassifier
from causalml.inference.meta import BaseTRegressor, BaseXRegressor, BaseRRegressor
from scipy import stats
from lightgbm import LGBMClassifier, LGBMRegressor
import numpy as np

from causalml.propensity import ElasticNetPropensityModel
from causalml.match import NearestNeighborMatch, create_table_one
import pandas as pd

metalearner_cls_dict = {'t-learner' : BaseTClassifier,
                        'x-learner' : BaseXClassifier,
                        'r-learner' : BaseRClassifier}
metalearner_reg_dict = {'t-learner' : BaseTRegressor,
                        'x-learner' : BaseXRegressor,
                        'r-learner' : BaseRRegressor}

class CausalModel:
    """
    Infers causality from the data contained in `df` using a metalearner.
    The `treat_col` column should contain binary values: 1 for treated, 0 for untreated.
    The `outcome_col` column should contain the outcome values, which can be either numeric (ints or floats)
    or categorical (strings).
    The `text_col` column contains the text values (e.g., articles, reviews, emails).
    All other columns are treated as additional numerical or categorical covariates unless
    they appear in `ignore_cols`.
    The `learner` parameter can be used to supply a custom learner to the metalearner.
    Example: `learner = LGBMClassifier(n_estimators=1000)`
    """
    def __init__(self,
                 df,
                 treatment_col='treatment',
                 outcome_col='outcome',
                 text_col='text',
                 ignore_cols=[],
                 learner = None,
                 treatment_effect_col = 'treatment_effect',
                 verbose=1):
        """
        constructor
        """

        self.treatment_col = treatment_col
        self.outcome_col = outcome_col
        self.text_col = text_col # currently ignored
        self.ignore_cols = ignore_cols
        self.te = treatment_effect_col
        self.v = verbose
        self.df = df.copy()

        # these are auto-populated by preprocess method
        self.is_classification = True
        self.feature_names = None
        self.x = None
        self.y = None
        self.treatment = None

        # preprocess
        self.preprocess(self.df)

        # setup model
        metalearner_type = 't-learner' # support T-Learners for now
        if self.is_classification:
            learner = LGBMClassifier() if learner is None else learner
            metalearner_cls = metalearner_cls_dict[metalearner_type]
        else:
            learner = LGBMRegressor() if learner is None else learner
            metalearner_cls = metalearner_reg_dict[metalearner_type]
        if metalearner_cls in [BaseTClassifier, BaseTRegressor]:
            self.model = metalearner_cls(learner=learner,control_name=0)
        else:
            self.model = metalearner_cls(outcome_learner=learner,
                                     effect_learner=learner,
                                     control_name=0)


    def preprocess(self, df=None, na_cont_value=-1, na_cat_value='MISSING'):
        """
        Preprocess a dataframe for causal inference.
        If df is None, uses self.df.
        """
        start_time = time.time()

        # step 1: check/clean dataframe
        if not isinstance(df, pd.DataFrame):
            raise ValueError('df must be a pandas DataFrame')
        df = df.rename(columns=lambda x: x.strip()) # strip headers
        df = df.applymap(lambda x: x.strip() if isinstance(x, str) else x)  # strip data
        df, _ = self._preprocess_column(df, self.treatment_col, is_treatment=True)
        df, self.is_classification = self._preprocess_column(df, self.outcome_col, is_treatment=False)
        self.feature_names = [c for c in df.columns.values \
                             if c not in [self.treatment_col, self.outcome_col]+self.ignore_cols]
        self.x = df[self.feature_names].copy()
        self.y = df[self.outcome_col].copy()
        self.treatment = df[self.treatment_col].copy()

        # step 2: fill empty values on x
        for c in self.feature_names:
            if self._check_type(df, c)['dtype'] =='string': self.x[c] = self.x[c].fillna(na_cat_value)
            if self._check_type(df, c)['dtype']=='numeric': self.x[c] = self.x[c].fillna(na_cont_value)

        # step 3: one-hot encode categorial features
        for c in self.feature_names:
            if self._check_type(df, c)['dtype']=='string':
                self.x = self.x.merge(pd.get_dummies(self.x[c], prefix = c, drop_first=True), left_index=True, right_index=True)
                del self.x[c]
        self.feature_names_one_hot = self.x.columns
        if self.v: print('outcome is: %s' % ('categorical' if self.is_classification else 'numerical'))
        if self.v: print("preprocess time: ", -start_time + time.time()," sec")

        return df


    def _preprocess_column(self, df, col, is_treatment=True):
        """
        Preprocess treatment and outcome columns.
        """
        # remove nulls
        df = df[df[col].notnull()]

        # check if already binarized
        if self._check_binary(df, col): return df, True

        # inspect column
        d = self._check_type(df, col)
        typ = d['dtype']
        num = d['nunique']

        # process as treatment
        if is_treatment:
            if typ == 'numeric' or (typ == 'string' and num != 2):
                raise ValueError('Treatment column must contain only two unique values ' +\
                                 'indicating the treated and control groups.')
            values = sorted(df[col].unique())
            df[col].replace(values, [0,1], inplace=True)
            if self.v: print('replaced %s in column "%s" with %s' % (values, col, [0,1]))
        # process as outcome
        else:
            if typ == 'string' and num != 2:
                raise ValueError('If the outcome column is string/categorical, it must '+
                                'contain only two unique values.')
            if typ == 'string':
                values = sorted(df[col].unique())
                df[col].replace(values, [0,1], inplace=True)
                if self.v: print('replaced %s in column "%s" with %s' % (values, col, [0,1]))
        return df, self._check_binary(df, col)


    def _check_type(self, df, col):
        from pandas.api.types import is_string_dtype
        from pandas.api.types import is_numeric_dtype
        dtype = None

        tmp_var = df[df[col].notnull()][col]
        #if tmp_var.nunique()<=5: return 'cat'
        if is_numeric_dtype(tmp_var): dtype = 'numeric'
        elif is_string_dtype(tmp_var): dtype =  'string'
        else:
            raise ValueError('Columns in dataframe must be either numeric or strings.  ' +\
                             'Column %s is neither' % (col))
        output = {'dtype' : dtype, 'nunique' : tmp_var.nunique()}
        return output


    def _check_binary(self, df, col):
        return df[col].isin([0,1]).all()

    def _get_feature_names(self, df):
        return [c for c in df.columns.values \
                if c not in [self.treatment_col, self.outcome_col]+self.ignore_cols]

    def fit(self):
        print("start fitting causal model")
        start_time = time.time()
        self.model.fit(self.x.values, self.treatment.values, self.y.values)
        preds = self.predict(self.x)
        self.df[self.te] = preds
        print("time to fit causalmodel: ",-start_time + time.time()," sec")

    def predict(self, x):
        if isinstance(x, pd.DataFrame):
            return self.model.predict(x.values)
        else:
            return self.model.predict(x)

    def estimate_ate(self, bool_mask=None):
        df = self.df if bool_mask is None else self.df[bool_mask]
        a = df[self.te].values
        mean = np.mean(a)
        return {'ate' : mean}



    def minimize_bias(self, caliper = None):
            print('-------Start bias minimization procedure----------')
            start_time = time.time()
            #Join x, y and treatment vectors
            df_match = self.x.merge(self.treatment,left_index=True, right_index=True)
            df_match = df_match.merge(self.y, left_index=True, right_index=True)

            #buld propensity model. Propensity is the probability of raw belongs to control group.
            pm = ElasticNetPropensityModel(n_fold=3, random_state=42)

            #ps - propensity score
            df_match['ps'] = pm.fit_predict(self.x, self.treatment)

            #Matching model object
            psm = NearestNeighborMatch(replace=False,
                           ratio=1,
                           random_state=423,
                           caliper=caliper)

            ps_cols = list(self.feature_names_one_hot)
            ps_cols.append('ps')

            #Apply matching model
            #If error, then sample is unbiased and we don't do anything
            self.flg_bias = True
            self.df_unbiased = psm.match(data=df_match, treatment_col='treatment',score_cols=['ps'])
            self.x_unbiased = self.df_unbiased[self.x.columns]
            self.y_unbiased = self.df_unbiased[self.outcome_col]
            self.treatment_unbiased = self.df_unbiased['treatment']
            print('-------------------MATCHING RESULTS----------------')
            print('-----BEFORE MATCHING-------')
            print(create_table_one(data=df_match,
                                    treatment_col='treatment',
                                    features=list(self.feature_names_one_hot)))
            print('-----AFTER MATCHING-------')
            print(create_table_one(data=self.df_unbiased,
                                    treatment_col='treatment',
                                    features=list(self.feature_names_one_hot)))
            return self.df_unbiased
