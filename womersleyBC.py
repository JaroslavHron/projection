from __future__ import print_function

__author__ = 'jh'
from dolfin import Expression, near, pi
from sympy import I, re, sqrt, exp, symbols, lambdify, besselj
from scipy.special import jv

factor = 0.0

R = 5.0
r, tm = symbols('r tm')
u = (-43.2592 * r ** 2 +
     (-11.799 + 0.60076 * I) * ((0.000735686 - 0.000528035 * I)
                                * besselj(0, r * (1.84042 + 1.84042 * I)) + 1) * exp(-8 * I * pi * tm) +
     (-11.799 - 0.60076 * I) * ((0.000735686 + 0.000528035 * I)
                                * besselj(0, r * (1.84042 - 1.84042 * I)) + 1) * exp(8 * I * pi * tm) +
     (-26.3758 - 4.65265 * I) * (-(0.000814244 - 0.00277126 * I)
                                 * besselj(0, r * (1.59385 - 1.59385 * I)) + 1) * exp(6 * I * pi * tm) +
     (-26.3758 + 4.65265 * I) * (-(0.000814244 + 0.00277126 * I)
                                 * besselj(0, r * (1.59385 + 1.59385 * I)) + 1) * exp(-6 * I * pi * tm) +
     (-51.6771 + 27.3133 * I) * (-(0.0110653 - 0.00200668 * I)
                                 * besselj(0, r * (1.30138 + 1.30138 * I)) + 1) * exp(-4 * I * pi * tm) +
     (-51.6771 - 27.3133 * I) * (-(0.0110653 + 0.00200668 * I)
                                 * besselj(0, r * (1.30138 - 1.30138 * I)) + 1) * exp(4 * I * pi * tm) +
     (-33.1594 - 95.2423 * I) * ((0.0314408 - 0.0549981 * I)
                                 * besselj(0, r * (0.920212 - 0.920212 * I)) + 1) * exp(2 * I * pi * tm) +
     (-33.1594 + 95.2423 * I) * ((0.0314408 + 0.0549981 * I)
                                 * besselj(0, r * (0.920212 + 0.920212 * I)) + 1) * exp(
          -2 * I * pi * tm) + 1081.48)
# how this works?
u_lambda = lambdify([r, tm], u, ['numpy', {'besselj': jv}])


class WomersleyProfile(Expression):
    def __init__(self):
        self.t = 0

    def eval(self, value, x):
        rad = float(sqrt(x[0] * x[0] + x[1] * x[
            1]))  # conversion to float needed, u_lambda (and near) cannot use sympy Float as input
        value[0] = 0
        value[1] = 0
        value[2] = 0 if near(rad, R) else re(factor * u_lambda(rad, self.t))  # do not evaluate on boundaries, it's 0

    def value_shape(self):
        return (3,)


