import React from 'react';
export const Input = ({ className = '', ...props }: any) => (
  <input className={`border p-2 rounded w-full ${className}`} {...props} />
);
