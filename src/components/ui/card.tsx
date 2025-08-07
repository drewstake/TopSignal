import React from 'react';
export function Card({ children, className }: any) {
  return <div className={`p-4 bg-white rounded shadow ${className}`}>{children}</div>;
}
export const CardHeader = ({ children }: any) => <div className="mb-2 font-bold">{children}</div>;
export const CardTitle = ({ children }: any) => <h3>{children}</h3>;
export const CardContent = ({ children }: any) => <div>{children}</div>;
