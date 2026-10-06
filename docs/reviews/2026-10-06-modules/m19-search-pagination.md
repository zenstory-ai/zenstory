# M19 W1 automatic-search page reset

Actual Grid/hook/QueryClient/API boundary default and Strict showed two genuine
REDs: page2 navigation was healthy, but automatic narrowing search retained2
even when matches existed only on1. Approved minimum inlineinput handler also
sets page1; debounce300ms, submit/pagination/cache/API unchanged.

Fresh 79 affected Grid/hook cases pass, scopedGrid88.73L74.46B91.66F89.18S
exceeds unchanged68/55/61/66. New current-page typing, clearing and submit
controls retain page1, exactlyone narrowed query, no repeated fresh-cached
old-page1 network fetch. QAtypes/lint and consolidated source/build receipts
under ../m07-m19-web-consolidated; independent SOURCE_APPROVE/CLEAR.
No hook/framework/config/dependency/provider/Actions changes.
