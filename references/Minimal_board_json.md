Minimal board.json needed to import a Study Designer board:

{  
 "labels": \[\], —\> Even if labels aren’t used, we need to set this empty array for import to work  
 "lists": \[ –\> lists represent Events in Study Designer  
   {  
     "\_id": "SsuCme3ZpkRFP4Qag", \-\> 17 character unique ID generated using [Meteor Random-Id function](https://docs.meteor.com/packages/random#Random-id)  
     "title": "Event1", –\> Title of the event  
     "sort": 0, –\> Order of the event in Study Designer  
     "eventOcoid": "SE\_EVENT1", –\> Event OID  
     "isRepeating": false, –\> Whether the event is repeating or not  
     "type": "Visit-Based" –\> Whether the event is Visit-Based or Common event  
   }  
 \],  
 "cards": \[ –\> cards represent Forms in Study Designer  
   {  
     "\_id": "m4CwucePB5swL3mY8", –\> 17 character unique ID generated using [Meteor Random-Id function](https://docs.meteor.com/packages/random#Random-id)

     "title": "Form1", –\> Title of the form  
     "listId": "SsuCme3ZpkRFP4Qag", –\> ID of the event to which the form belongs.  
     "formOcoid": "F\_FORM1", –\> Form OID  
     "sort": 0, –\> Order of the form card within an event.  
     "\_parentId": null \-\> References \_id of the card that this card is a copy of. Null if it’s the original card. 

   }  
 \]  
}

Example board.json that creates a copy of SV1223 study in QE1 staging.

{  
 "labels": \[\],  
 "lists": \[  
   {  
     "\_id": "SsuCme3ZpkRFP4Qag",  
     "title": "Event1",  
     "sort": 0,  
     "eventOcoid": "SE\_EVENT1",  
     "isRepeating": false,  
     "type": "Visit-Based"  
   },  
   {  
     "\_id": "Aop8RcqTs6HreXKxx",  
     "title": "Event2",  
     "sort": 1,  
     "eventOcoid": "SE\_EVENT2",  
     "isRepeating": false,  
     "type": "Common"  
   },  
   {  
     "\_id": "vFmjy7365NWkrMKbS",  
     "title": "Event3",  
     "sort": 2,  
     "eventOcoid": "SE\_EVENT3",  
     "isRepeating": true,  
     "type": "Visit-Based"  
   }  
 \],  
 "cards": \[  
   {  
     "\_id": "m4CwucePB5swL3mY8",  
     "title": "Form1",  
     "listId": "SsuCme3ZpkRFP4Qag",  
     "formOcoid": "F\_FORM1",  
     "sort": 0  
   },  
   {  
     "\_id": "wrGMNh5o4T8wD59XZ",  
     "title": "Form1",  
     "listId": "Aop8RcqTs6HreXKxx",  
     "formOcoid": "F\_FORM1",  
     "sort": 0,  
     "\_parentId": "m4CwucePB5swL3mY8"  
   },  
   {  
     "\_id": "euEwFj7tRL2auQ8r7",  
     "title": "Form2",  
     "listId": "Aop8RcqTs6HreXKxx",  
     "formOcoid": "F\_FORM2",  
     "sort": 1  
   },  
   {  
     "\_id": "9GxkrptNMTkmNCPDF",  
     "title": "Form3",  
     "listId": "vFmjy7365NWkrMKbS",  
     "formOcoid": "F\_FORM3",  
     "sort": 0  
   }  
 \]  
}  
